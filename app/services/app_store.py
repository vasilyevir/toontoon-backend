"""Подпись App Store: узнать человека по покупке, а не по паролю.

Вход из продукта убран. Человека узнаёт App Store: покупка привязана к его
Apple ID, и `originalTransactionId` опознаёт его на любом устройстве — без
пароля, без второй учётной записи и без экрана перед продуктом.

Верить приложению на слово тут нельзя ни минуты: строка с чужим
`originalTransactionId`, отправленная руками, отдала бы чужой аккаунт вместе с
работами и балансом. Поэтому чек проверяется по подписи.

StoreKit 2 отдаёт транзакцию как JWS: заголовок, тело и подпись, а в заголовке
— цепочка сертификатов `x5c` от листа до корня Apple. Проверяем всё звено за
звеном:

1. Корень цепочки совпадает с приложенным здесь Apple Root CA G3 — байт в байт.
   Не «выпущен доверенным центром», а именно этот сертификат: доверять чужому
   корню значит не проверять вовсе.
2. Каждый сертификат подписан следующим.
3. Каждый действует по датам.
4. Подпись самого JWS сделана ключом листа.
5. `bundleId` в теле — наш.

Только после этого телу можно верить.
"""
from __future__ import annotations

import base64
import json
import pathlib
from datetime import datetime, timezone

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.config import settings

# Корень Apple, приложенный к коду. Забирать его из сети в момент проверки
# значило бы доверять сети ровно там, где мы проверяем доверие.
_CERTS = pathlib.Path(__file__).parent / "certs"
_ROOT_PEM = (_CERTS / "apple_root_ca_g3.pem").read_bytes()

# Корень локального StoreKit из Xcode. Им подписаны чеки, выданные локальной
# конфигурацией покупок, — настоящие чеки настоящей формы, но своего корня.
#
# Принимается только при `accept_storekit_test_root`, и настройка эта в проде
# обязана быть выключена: сертификат лежит внутри Xcode у всех, и с ним подписку
# себе выпишет кто угодно.
_TEST_ROOT_PEM = (_CERTS / "storekit_test_root.pem").read_bytes()


def _trusted_roots() -> list[bytes]:
    roots = [_ROOT_PEM]
    if settings.accept_storekit_test_root:
        roots.append(_TEST_ROOT_PEM)
    return roots


class BadTransaction(Exception):
    """Чек не прошёл проверку. Текст — для журнала, не для человека."""


def _b64url(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _verify_signed_by(child: x509.Certificate, parent: x509.Certificate) -> None:
    """Подписан ли `child` ключом `parent`."""
    key = parent.public_key()
    try:
        if isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes,
                       ec.ECDSA(child.signature_hash_algorithm))
        elif isinstance(key, rsa.RSAPublicKey):
            key.verify(child.signature, child.tbs_certificate_bytes,
                       padding.PKCS1v15(), child.signature_hash_algorithm)
        else:
            raise BadTransaction(f"неизвестный тип ключа: {type(key).__name__}")
    except InvalidSignature as exc:
        raise BadTransaction("звено цепочки подписано не тем ключом") from exc


def _check_dates(cert: x509.Certificate, now: datetime) -> None:
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        raise BadTransaction(f"сертификат недействителен по датам: {cert.subject.rfc4514_string()}")


def describe_chain(signed: str) -> str:
    """Из чего сложен чек — для журнала, когда проверка его отвергла.

    Читает заголовок БЕЗ проверки подписи и ничего не решает: строка отсюда
    годится только в лог. Причина отказа говорит, что не сошлось; эта строка —
    с чем именно, и без неё каждый отказ стоит поездки к разработчику.

    Нужна ровно один раз в жизни: первая покупка через локальный StoreKit.
    Цепочка Xcode нами не виденa — корень у неё RSA, а не эллиптический, как
    у Apple, и длину её мы знаем только по косвенным признакам.
    """
    try:
        header = json.loads(_b64url(signed.split(".")[0]))
    except Exception:  # noqa: BLE001 — это диагностика, падать ей нельзя
        return "заголовок не читается"

    bits = [f"alg={header.get('alg')}"]
    chain_raw = header.get("x5c") or []
    bits.append(f"звеньев={len(chain_raw)}")
    for i, raw in enumerate(chain_raw):
        try:
            cert = x509.load_der_x509_certificate(base64.b64decode(raw))
        except Exception:  # noqa: BLE001
            bits.append(f"[{i}] не читается")
            continue
        key = type(cert.public_key()).__name__.replace("PublicKey", "")
        cn = next((a.value for a in cert.subject if a.oid == x509.NameOID.COMMON_NAME), "?")
        bits.append(f"[{i}] {cn!r} ключ={key}")
    return "; ".join(bits)


# Имя, которым StoreKit из Xcode подписывает свои чеки.
_LOCAL_TEST_CN = "StoreKit Testing in Xcode"


def _is_local_test_receipt(chain: list[x509.Certificate]) -> bool:
    """Чек, выписанный проверкой StoreKit внутри Xcode.

    Такой чек нельзя привязать к корню, и это не наша недоработка. Xcode
    подписывает его сертификатом, который создаёт у себя: в цепочке одно звено,
    самоподписанное, эллиптическое, с именем «StoreKit Testing in Xcode». В
    связке ключей его нет, в самом Xcode лежит другой — RSA с именем
    «StoreKit», — и к присланному он отношения не имеет. Постоянного корня,
    к которому можно привязаться, просто не существует.

    Поэтому здесь честнее сказать вслух, что проверяется, а что нет. Мы
    убеждаемся, что чек внутренне сходится: подпись отвечает ключу из
    собственного сертификата, срок не вышел, приложение наше. Мы НЕ убеждаемся,
    что его выписала Apple — такой чек может выписать кто угодно, у кого есть
    Xcode.

    Ровно об этом и предупреждает `ACCEPT_STOREKIT_TEST_ROOT`: с ним подписку
    себе выпишет любой. Флаг выключен по умолчанию, а при DEBUG=false о нём
    кричит проверка при старте.
    """
    if not settings.accept_storekit_test_root or len(chain) != 1:
        return False
    only = chain[0]
    name = next((a.value for a in only.subject if a.oid == x509.NameOID.COMMON_NAME), "")
    return name == _LOCAL_TEST_CN and only.subject == only.issuer


def verify_jws(signed: str, *, now: datetime | None = None) -> dict:
    """Проверить подпись Apple и вернуть тело. Что в теле — решает вызывающий.

    Тем же ключом подписаны и чек о покупке, и уведомление о продлении, и
    вложенная в него транзакция. Проверка у всех троих одна, а смысл разный —
    поэтому она здесь одна, а разбор смысла снаружи.

    Бросает `BadTransaction` на любом сомнении. Промежуточных состояний нет:
    подпись либо доказана, либо не считается.
    """
    now = now or datetime.now(timezone.utc)
    parts = signed.split(".")
    if len(parts) != 3:
        raise BadTransaction("это не JWS")
    header_raw, payload_raw, signature_raw = parts

    try:
        header = json.loads(_b64url(header_raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadTransaction("заголовок не читается") from exc

    if header.get("alg") != "ES256":
        raise BadTransaction(f"чужой алгоритм подписи: {header.get('alg')}")

    chain_raw = header.get("x5c") or []
    if not chain_raw:
        raise BadTransaction("в заголовке нет сертификатов")
    try:
        chain = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain_raw]
    except Exception as exc:  # noqa: BLE001
        raise BadTransaction("сертификаты не читаются") from exc

    for cert in chain:
        _check_dates(cert, now)

    if _is_local_test_receipt(chain):
        # Дальше — только подпись листа. Цепочки доверия здесь нет и быть не
        # может: см. `_is_local_test_receipt`.
        pass
    else:
        if len(chain) < 2:
            raise BadTransaction("в заголовке нет цепочки сертификатов")
        # Корень сверяем побайтно. «Тоже от Apple» и «этот самый» — разные вещи,
        # и вторая проверяется только так.
        der = serialization.Encoding.DER
        ours = chain[-1].public_bytes(encoding=der)
        known = [x509.load_pem_x509_certificate(pem).public_bytes(encoding=der)
                 for pem in _trusted_roots()]
        if ours not in known:
            raise BadTransaction("цепочка ведёт не к нашему корню")
        for child, parent in zip(chain, chain[1:]):
            _verify_signed_by(child, parent)

    leaf_key = chain[0].public_key()
    if not isinstance(leaf_key, ec.EllipticCurvePublicKey):
        raise BadTransaction("лист подписан не эллиптическим ключом")

    # Подпись JWS лежит в формате JOSE — два числа подряд; проверяльщику нужен
    # DER.
    raw = _b64url(signature_raw)
    if len(raw) != 64:
        raise BadTransaction("длина подписи не та")
    half = len(raw) // 2
    der = encode_dss_signature(int.from_bytes(raw[:half], "big"),
                               int.from_bytes(raw[half:], "big"))
    try:
        leaf_key.verify(der, f"{header_raw}.{payload_raw}".encode(),
                        ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise BadTransaction("подпись не сходится") from exc

    try:
        return json.loads(_b64url(payload_raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadTransaction("тело не читается") from exc


def _check_bundle(payload: dict, where: str) -> None:
    """Наше ли это приложение.

    Чужая программа подписана тем же корнем Apple, и цепочка у неё безупречная.
    Без этой сверки её покупка открывала бы дверь сюда.

    Незаданный `apple_bundle_id` — отказ, а не разрешение. Раньше здесь стояло
    `if wanted and ...`: пустая настройка ОТКЛЮЧАЛА проверку целиком, то есть
    забытая переменная окружения открывала дверь любой покупке из любого
    приложения App Store. Отсутствие настройки — причина не пустить, а не
    причина пустить всех.
    """
    wanted = (settings.apple_bundle_id or "").strip()
    if not wanted:
        raise BadTransaction(
            "APPLE_BUNDLE_ID не задан: сверить приложение не с чем, "
            f"{where} не принимается")
    if payload.get("bundleId") != wanted:
        raise BadTransaction(f"{where} от другого приложения: {payload.get('bundleId')}")


def _check_environment(payload: dict, where: str) -> None:
    """Настоящая ли это покупка или из песочницы.

    Чеки песочницы подписаны ТЕМ ЖЕ корнем Apple, и цепочка у них безупречная —
    отличает их единственное поле. Поле это читалось и клалось в базу, но не
    проверялось нигде: покупка в песочнице бесплатна, и подписку себе выписывал
    любой, у кого есть сборка из TestFlight или пересобранный IPA.

    Принимается только явное `production`. Пустое поле — тоже отказ: это
    граница, за которой деньги, и «не знаю, откуда чек» здесь означает «не
    принимаю», а не «наверное, настоящий».

    `accept_storekit_test_root` снимает проверку целиком — тот флаг и так
    означает «принимаю чеки, выписанные кем угодно с Xcode», и в проде он
    обязан быть выключен (о чём кричит проверка при старте).
    """
    if settings.accept_storekit_test_root or settings.debug:
        return
    env = str(payload.get("environment") or "").strip().lower()
    allowed = {"production"} | ({"sandbox"} if settings.accept_sandbox_receipts else set())
    if env not in allowed:
        raise BadTransaction(f"{where} не из App Store, а из {env or 'неизвестно откуда'}")


def verify_transaction(signed: str, *, now: datetime | None = None) -> dict:
    """Чек о покупке: подпись, наше приложение, настоящая покупка, имя внутри."""
    payload = verify_jws(signed, now=now)
    _check_bundle(payload, "чек")
    _check_environment(payload, "чек")
    if not payload.get("originalTransactionId"):
        raise BadTransaction("в чеке нет originalTransactionId")
    return payload


def verify_notification(signed: str, *, now: datetime | None = None) -> dict:
    """Уведомление App Store о продлении, отмене или возврате.

    Возвращает разобранное: тип, подтип, идентификатор доставки и уже
    проверенную транзакцию внутри. Вложенная транзакция — отдельный JWS с той
    же подписью, и верить ей на слово нельзя ровно так же: сама обёртка
    подписана Apple, но пересобрать её содержимое, оставив подпись обёртки,
    нельзя только потому, что мы проверяем и вложенное.

    Уведомление без транзакции внутри — не наше дело: гранты и возвраты живут
    в ней, а без неё нечего и применять.
    """
    outer = verify_jws(signed, now=now)
    data = outer.get("data") or {}
    _check_bundle(data, "уведомление")
    # Уведомления о песочнице Apple шлёт на тот же адрес. Применять их к
    # настоящим подпискам нельзя ровно по той же причине, что и чеки.
    _check_environment(data, "уведомление")

    inner = data.get("signedTransactionInfo")
    if not inner:
        raise BadTransaction("в уведомлении нет транзакции")
    transaction = verify_jws(str(inner), now=now)
    if not transaction.get("originalTransactionId"):
        raise BadTransaction("в транзакции уведомления нет originalTransactionId")

    return {
        "type": str(outer.get("notificationType") or ""),
        "subtype": str(outer.get("subtype") or "") or None,
        # Apple повторяет доставку, пока не получит 200. Идентификатор — то,
        # по чему повтор отличается от второго события.
        "uuid": str(outer.get("notificationUUID") or ""),
        "environment": str(data.get("environment") or "").lower() or None,
        "transaction": transaction,
    }
