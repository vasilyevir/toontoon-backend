"""Сервер сам ставит себе диагноз.

Отказы должны находить нас, а не мы их. Первый шаг к этому — не канал
доставки, а сам приговор: пока наружу отдаются сырые числа, читать их всё
равно приходится человеку, а он читает раз в день, если вспомнит.

Здесь считается ответ на один вопрос: всё ли в порядке прямо сейчас, и если
нет — чем именно нехорошо. Ответ годится и сторожевому сервису (он смотрит на
код ответа), и человеку (он читает причины словами).

Что считается бедой и почему именно это:

* **Доля отказов.** Не число, а доля: две неудачи из трёх и две из трёхсот —
  разные новости. Порог вчетверо выше того, что видно в сводке за неделю.
* **Оборвавшиеся работы.** Человек заплатил, кадра нет, и никто об этом не
  узнает, пока не придёт сверка. Одна такая — уже повод посмотреть.
* **Невозвращённые деньги.** Работа не получилась, а TOONTOON не вернулись.
  Это долг перед конкретным человеком, и сам он не рассасывается.
* **Молчание.** Ни одной работы за сутки там, где работы были, значит, что
  сломалось раньше генерации — и по отказам этого не видно, потому что
  отказов тоже нет.

Порогов немного и они грубые. Точные появятся, когда будет что мерить: пока
единственный источник — проверки разработчика, а не живой спрос.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import models as m

# Доля отказов, выше которой это уже не невезение.
FAILURE_RATE_ALARM = 0.20
# Сколько часов тишины считать подозрительными. Сутки: ночь без единой работы
# — обычное дело, а вот сутки подряд — уже нет.
SILENCE_HOURS = 24


def сколько(n: int, одна: str, две: str, много: str) -> str:
    """«1 работа», «2 работы», «5 работ».

    Мелочь, но эту строку человек читает ночью из письма от сторожа, и
    «1 работ(ы)» в ней выглядит как черновик, забытый в проде.
    """
    хвост = n % 100
    if 11 <= хвост <= 14:
        слово = много
    elif n % 10 == 1:
        слово = одна
    elif 2 <= n % 10 <= 4:
        слово = две
    else:
        слово = много
    return f"{n} {слово}"


@dataclass
class Diagnosis:
    """Приговор целиком: одно слово наружу и причины словами."""

    ok: bool = True
    reasons: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    def fault(self, why: str) -> None:
        self.ok = False
        self.reasons.append(why)

    @property
    def status(self) -> str:
        return "ok" if self.ok else "degraded"

    def as_dict(self) -> dict:
        return {"status": self.status, "reasons": self.reasons, "facts": self.facts}


def _recent():
    """Работы за последние сутки.

    Время берётся у базы, а не у процесса: часы приложения и часы Postgres
    расходятся, и на сутках это уже заметно.
    """
    return m.Generation.created_at > func.now() - timedelta(hours=SILENCE_HOURS)


def _n(condition):
    return func.count().filter(condition)


async def diagnose(session: AsyncSession) -> Diagnosis:
    """Посмотреть на последние сутки и сказать, всё ли в порядке.

    Одним запросом, а не четырьмя: диагноз спрашивают часто — сторож раз в
    минуту, — и четыре обхода таблицы работ на каждый его вопрос стоят дороже,
    чем сам ответ.
    """
    recent = _recent()
    # Долг считается тем же способом, что и в сверке: неудавшаяся работа, у
    # которой в книге нет проводки возврата по её платежу.
    payment = m.Generation.request_params["payment_id"].astext
    refunded = (
        select(m.WalletLedger.id)
        .where(m.WalletLedger.ref_id == payment, m.WalletLedger.reason == "refund")
        .exists()
    )
    stale = m.Generation.created_at < func.now() - timedelta(
        minutes=settings.stale_generation_minutes)

    total, done, failed, stuck, owed, ever = (await session.execute(select(
        _n(recent),
        _n(recent & (m.Generation.status == "done")),
        _n(recent & (m.Generation.status == "failed")),
        _n((m.Generation.status == "running") & stale),
        _n(
            (m.Generation.status == "failed")
            & (m.Generation.cost > 0)
            & payment.isnot(None)
            & ~refunded
        ),
        func.count(),
    ))).one()

    return judge({
        "работ_за_сутки": total,
        "удалось": done,
        "отказов": failed,
        "оборвалось": stuck,
        "не_вернули_денег": owed,
    }, ever=bool(ever))


def judge(facts: dict, *, ever: bool) -> Diagnosis:
    """Приговор по подсчитанному.

    Отдельно от запроса, потому что правила надо проверять точно, а числа —
    подкладывать. «За сутки ни одной работы» иначе проверялось бы только
    опустошением базы, а такой тест никто не станет запускать дважды.

    `ever` — были ли работы вообще: без него тишина на пустой базе выглядит
    как поломка, и сторож приучается к ложной тревоге в первый же день.
    """
    done, failed = facts["удалось"], facts["отказов"]
    finished = done + failed
    rate = (failed / finished) if finished else 0.0

    d = Diagnosis(facts={**facts, "доля_отказов": round(rate, 3)})

    if finished and rate > FAILURE_RATE_ALARM:
        d.fault(f"каждая {round(1 / rate)}-я работа не получается "
                f"({failed} из {finished} за сутки)")

    if facts["оборвалось"]:
        n = facts["оборвалось"]
        d.fault(f"{сколько(n, 'работа оборвалась', 'работы оборвались', 'работ оборвались')} "
                f"и висят дольше {settings.stale_generation_minutes} мин — "
                "за них заплачено")

    if facts["не_вернули_денег"]:
        n = facts["не_вернули_денег"]
        d.fault(f"{сколько(n, 'работа не получилась', 'работы не получились', 'работ не получились')}"
                ", а деньги не вернулись")

    # Тишина — беда только там, где работы были. На пустой базе это норма, и
    # кричать на неё значит приучить сторожа к ложной тревоге.
    if facts["работ_за_сутки"] == 0 and ever:
        d.fault(f"за {SILENCE_HOURS} часов ни одной работы — "
                "похоже, сломалось что-то раньше генерации")

    return d
