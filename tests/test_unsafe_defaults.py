"""Умолчания, которые нельзя вернуть обратно не заметив.

Речь не о вкусах, а об одном классе ошибки: переменную окружения забыли.
Забывают их всегда, и вопрос только в том, чем оборачивается забывчивость —
службой, которая не работает, или службой, которая работает и отдаёт лишнее.

Каждое из этих умолчаний однажды стояло наоборот. Тест не даёт вернуть их
молча: перевернуть можно, но придётся стереть отсюда строку и прочитать, что
в ней написано.
"""
from __future__ import annotations

import logging

import pytest

from app.config import Settings


def bare() -> Settings:
    """Настройки, как их увидит свежий контейнер: без .env, только умолчания."""
    return Settings(_env_file=None)


def test_a_forgotten_deployment_does_not_hand_out_password_reset_tokens():
    # С true ручка /auth/forgot-password возвращала токен сброса прямо в ответе.
    # Открытый запрос с чужой почтой — и аккаунт чужой.
    assert bare().expose_dev_tokens is False


def test_a_forgotten_deployment_does_not_accept_receipts_signed_by_xcode():
    # Сертификат локального StoreKit лежит внутри Xcode у всех, кто его ставил.
    # С true подписку себе выписывает любой, кто до него дошёл.
    assert bare().accept_storekit_test_root is False


def test_the_startup_checks_are_armed_by_default():
    # debug не гейтит больше ничего — только эти две проверки. Пока он был true
    # по умолчанию, обе молчали именно там, где нужны: в развёртывании, где
    # переменные не задали.
    assert bare().debug is False


@pytest.mark.parametrize("flag", ["expose_dev_tokens", "accept_storekit_test_root"])
def test_leaving_a_debug_flag_on_in_production_is_shouted_about(flag, caplog, monkeypatch):
    """Умолчание ловит забывчивость, проверка при старте — намеренность.

    Ошибки разные, и закрывать их должно разное: одно не заменяет другое.

    Зовётся настоящая `warn_about_debug_flags`, а не её пересказ. Тест, который
    повторяет проверку своими словами, продолжает проходить и после того, как
    проверку удалили из приложения, — то есть охраняет собственный текст.
    """
    from app import main

    monkeypatch.setattr(main.settings, "debug", False)
    monkeypatch.setattr(main.settings, flag, True)

    with caplog.at_level(logging.ERROR, logger="toontoon"):
        main.warn_about_debug_flags()

    assert caplog.records, f"{flag}=true при DEBUG=false прошло молча"
    assert flag.upper() in caplog.records[0].message


def test_a_correctly_configured_production_start_is_quiet(caplog, monkeypatch):
    """Иначе предыдущий тест проходил бы и у функции, которая кричит всегда."""
    from app import main

    monkeypatch.setattr(main.settings, "debug", False)
    monkeypatch.setattr(main.settings, "expose_dev_tokens", False)
    monkeypatch.setattr(main.settings, "accept_storekit_test_root", False)

    with caplog.at_level(logging.ERROR, logger="toontoon"):
        main.warn_about_debug_flags()

    assert not caplog.records, "накричала на правильно настроенный прод"


def test_a_developer_machine_is_not_nagged(caplog, monkeypatch):
    """С DEBUG=true флаги включены осознанно — крик здесь был бы шумом."""
    from app import main

    monkeypatch.setattr(main.settings, "debug", True)
    monkeypatch.setattr(main.settings, "expose_dev_tokens", True)
    monkeypatch.setattr(main.settings, "accept_storekit_test_root", True)

    with caplog.at_level(logging.ERROR, logger="toontoon"):
        main.warn_about_debug_flags()

    assert not caplog.records
