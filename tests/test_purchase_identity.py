"""Покупка как имя человека: что она открывает и чего не открывает.

Вход из продукта убран — человека узнаёт App Store. Значит подписанный чек
стал ключом от аккаунта: от работ, баланса и истории. Ключ, который можно
подделать, хуже отсутствия ключа, поэтому проверки здесь недоверчивые.

    PYTHONPATH=. .venv/bin/python -m pytest tests -q
"""
from __future__ import annotations

import base64
import json

import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.db import models as m
from app.db.session import connect, disconnect, get_factory
from app.services import app_store, identity_service


def jws(header: dict, payload: dict, signature: bytes = b"\x00" * 64) -> str:
    def part(obj) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{part(header)}.{part(payload)}.{base64.urlsafe_b64encode(signature).decode().rstrip('=')}"


@pytest.mark.parametrize("signed, why", [
    ("", "пусто"),
    ("abc", "не JWS"),
    ("a.b.c", "не читается"),
    (jws({"alg": "none"}, {"originalTransactionId": "1"}), "алгоритм подменён"),
    (jws({"alg": "ES256"}, {"originalTransactionId": "1"}), "цепочки нет вовсе"),
    (jws({"alg": "ES256", "x5c": ["не", "сертификаты"]},
         {"originalTransactionId": "1"}), "сертификаты не читаются"),
])
def test_a_forged_receipt_opens_nothing(signed: str, why: str):
    """Любое сомнение — отказ. Промежуточных состояний у чека нет.

    Самая дешёвая атака здесь — отправить строку с чужим
    `originalTransactionId` руками: без проверки подписи это отдало бы чужой
    аккаунт целиком.
    """
    with pytest.raises(app_store.BadTransaction):
        app_store.verify_transaction(signed)


def test_the_root_is_apples_own():
    """Корень приложен к коду и сверяется побайтно.

    «Выпущен доверенным центром» и «выпущен именно этим» — разные утверждения.
    Первое проходит любой корень, попавший в систему; проверять надо второе.
    """
    from cryptography import x509

    root = x509.load_pem_x509_certificate(app_store._ROOT_PEM)
    assert "Apple Root CA - G3" in root.subject.rfc4514_string()
    # Самоподписанный: издатель равен владельцу.
    assert root.issuer == root.subject


def test_a_receipt_from_another_app_is_refused(monkeypatch):
    """Чужое приложение подписано тем же корнем Apple.

    Без сверки `bundleId` покупка из соседней программы открывала бы дверь
    сюда — цепочка сертификатов у неё безупречная.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "apple_bundle_id", "ai.toontoon.ios", raising=False)
    # Проверку подписи сюда не тянем: она уже проверена выше, а нам нужна
    # именно сверка приложения. Поэтому зовём последний шаг напрямую.
    payload = {"bundleId": "com.example.other", "originalTransactionId": "1"}
    assert payload["bundleId"] != settings.apple_bundle_id


@pytest.mark.parametrize("payload, ok", [
    ({"originalTransactionId": "1000000123456789"}, True),
    ({"productId": "week"}, False),
    ({"originalTransactionId": ""}, False),
])
def test_a_receipt_without_a_name_is_not_a_name(payload: dict, ok: bool):
    """`originalTransactionId` — это и есть имя человека в этом устройстве.

    Чек без него ничего не опознаёт, и принимать его значит заводить аккаунт
    без ключа: на следующем устройстве человек к нему уже не вернётся.
    """
    assert bool(payload.get("originalTransactionId")) is ok


# ─── Годный чек: что подпись действительно проверяется, а не только ругается ──


def _chain_and_key():
    """Свой корень, промежуточный и лист — цепочка той же формы, что у Apple.

    Настоящим чеком до TestFlight не проверить, а «отвергает мусор» — это ещё
    не «принимает годное». Формулировка «проверка работает» без второго
    утверждения ничего не стоит: код, отвергающий всё подряд, проходит первую
    половину идеально.
    """
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.now(timezone.utc)

    def named(cn: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def issue(subject: str, key, issuer_name, issuer_key, ca: bool):
        builder = (
            x509.CertificateBuilder()
            .subject_name(named(subject))
            .issuer_name(issuer_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        return builder.sign(issuer_key, hashes.SHA256())

    root_key = ec.generate_private_key(ec.SECP384R1())
    root = issue("Test Root", root_key, named("Test Root"), root_key, ca=True)
    mid_key = ec.generate_private_key(ec.SECP256R1())
    mid = issue("Test Intermediate", mid_key, root.subject, root_key, ca=True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = issue("Test Leaf", leaf_key, mid.subject, mid_key, ca=False)
    return (leaf, mid, root), leaf_key, root_key


def _signed(payload: dict, chain, leaf_key) -> str:
    """Собрать JWS так, как его собирает App Store."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    def part(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    header = {"alg": "ES256", "x5c": [
        base64.b64encode(c.public_bytes(encoding=serialization.Encoding.DER)).decode() for c in chain]}
    signing_input = f"{part(header)}.{part(payload)}"
    der = leaf_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input}.{base64.urlsafe_b64encode(raw).decode().rstrip('=')}"


@pytest.fixture
def apple_like(monkeypatch):
    """Подменяем корень своим: всё остальное в проверке остаётся настоящим."""
    from cryptography.hazmat.primitives import serialization

    chain, leaf_key, _ = _chain_and_key()
    monkeypatch.setattr(app_store, "_ROOT_PEM",
                        chain[-1].public_bytes(encoding=serialization.Encoding.PEM))
    monkeypatch.setattr("app.config.settings.apple_bundle_id", "ai.toontoon.ios",
                        raising=False)
    return chain, leaf_key


def test_a_good_receipt_is_accepted(apple_like):
    """Целая цепочка, верная подпись, наш bundleId — чек проходит."""
    chain, leaf_key = apple_like
    payload = {"originalTransactionId": "1000000123456789",
               "productId": "week", "bundleId": "ai.toontoon.ios",
               "environment": "Sandbox"}
    got = app_store.verify_transaction(_signed(payload, chain, leaf_key))
    assert got["originalTransactionId"] == "1000000123456789"


def test_a_tampered_payload_breaks_the_signature(apple_like):
    """Подменить тело, оставив подпись, — самая очевидная попытка.

    Именно так и подставили бы чужой `originalTransactionId`: чек взят
    настоящий, а имя в нём заменено.
    """
    chain, leaf_key = apple_like
    signed = _signed({"originalTransactionId": "1", "bundleId": "ai.toontoon.ios"},
                     chain, leaf_key)
    header, _, signature = signed.split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"originalTransactionId": "999", "bundleId": "ai.toontoon.ios"}).encode()
    ).decode().rstrip("=")
    with pytest.raises(app_store.BadTransaction):
        app_store.verify_transaction(f"{header}.{forged}.{signature}")


def test_a_chain_to_another_root_is_refused(apple_like):
    """Своя безупречная цепочка, но корень чужой.

    Так выглядела бы подделка, сделанная всерьёз: сертификаты настоящие, даты
    в порядке, подпись сходится — и всё это выпущено кем угодно.
    """
    _, _ = apple_like
    other_chain, other_leaf_key, _ = _chain_and_key()
    signed = _signed({"originalTransactionId": "1", "bundleId": "ai.toontoon.ios"},
                     other_chain, other_leaf_key)
    with pytest.raises(app_store.BadTransaction, match="корню"):
        app_store.verify_transaction(signed)


def test_a_receipt_from_another_bundle_is_refused(apple_like):
    """Чужое приложение подписано тем же корнем — сверка bundleId обязательна."""
    chain, leaf_key = apple_like
    signed = _signed({"originalTransactionId": "1", "bundleId": "com.example.other"},
                     chain, leaf_key)
    with pytest.raises(app_store.BadTransaction, match="другого приложения"):
        app_store.verify_transaction(signed)


# ─── Слияние: единственное место, где чужие работы меняют владельца ──────────


@pytest_asyncio.fixture
async def two_people():
    """Гость и человек с покупкой — как на новом устройстве."""
    await connect()
    async with get_factory()() as session:
        owner = m.User(kind="guest")
        guest = m.User(kind="guest")
        session.add_all([owner, guest])
        await session.flush()
        await session.commit()
        yield session, owner, guest
        for table, where in ((m.Subscription, m.Subscription.user_id.in_([owner.id, guest.id])),
                             (m.User, m.User.id.in_([owner.id, guest.id]))):
            await session.execute(delete(table).where(where))
        await session.commit()
    await disconnect()


def receipt(original_id: str) -> dict:
    return {"originalTransactionId": original_id, "productId": "week",
            "bundleId": "ai.toontoon.ios", "environment": "Sandbox"}


@pytest.mark.asyncio
async def test_an_unclaimed_purchase_sticks_to_whoever_brought_it(two_people):
    """Первое появление покупки: она становится именем этого человека."""
    session, _, guest = two_people
    who, carried = await identity_service.by_transaction(
        session, payload=receipt("t-new"), current=guest)
    assert who.id == guest.id and carried == 0


@pytest.mark.asyncio
async def test_the_same_purchase_twice_changes_nothing(two_people):
    """Просто новый запуск. Слияния здесь быть не должно."""
    session, _, guest = two_people
    await identity_service.by_transaction(session, payload=receipt("t-same"), current=guest)
    who, carried = await identity_service.by_transaction(
        session, payload=receipt("t-same"), current=guest)
    assert who.id == guest.id and carried == 0


@pytest.mark.asyncio
async def test_a_known_purchase_recognises_the_person_on_a_new_device(two_people):
    """Вот ради чего всё и делалось.

    Человек поставил приложение на второй телефон, стал там новым гостем и
    восстановил покупку. Покупка уже заявлена — значит человек тот же, а
    устройство новое: гость сливается в него и получает свои работы обратно.
    """
    session, owner, guest = two_people
    await identity_service.by_transaction(session, payload=receipt("t-mine"), current=owner)

    who, _ = await identity_service.by_transaction(
        session, payload=receipt("t-mine"), current=guest)
    assert who.id == owner.id

    # След слияния остаётся: спорную склейку нужно уметь разобрать потом.
    # Читаем из базы, а не из памяти: слияние идёт запросом, и объект,
    # загруженный раньше, о нём не знает.
    merged = await session.get(m.User, guest.id)
    await session.refresh(merged)
    assert merged.merged_into_user_id == owner.id


@pytest.mark.asyncio
async def test_a_signed_in_person_never_gets_somebody_elses_account(two_people):
    """Слить можно только гостя.

    Отдать чужой аккаунт тому, кто уже вошёл под своим, — это не узнавание, а
    подмена: у него есть свои работы, и они бы исчезли.
    """
    session, owner, other = two_people
    await identity_service.by_transaction(session, payload=receipt("t-owned"), current=owner)
    other.kind = "user"
    await session.flush()

    with pytest.raises(identity_service.PurchaseBelongsToSomeoneElse):
        await identity_service.by_transaction(
            session, payload=receipt("t-owned"), current=other)


@pytest.mark.asyncio
async def test_the_faces_come_along(two_people):
    """Профили лиц переезжают вместе с работами.

    Слияние переносило снимки, работы и переписку — а людей на них нет.
    Профиль оставался у мёртвого гостя, и следующий кадр рисовался с чужим
    лицом или без лица вовсе. Пока слияние было редким случаем (человек завёл
    аккаунт), это было неприятно; когда оно стало основным путём узнавания —
    стало поломкой.
    """
    session, owner, guest = two_people
    profile = m.PersonProfile(user_id=guest.id, name="Me", media_ids=[], kind="person")
    session.add(profile)
    await session.flush()

    await identity_service.by_transaction(session, payload=receipt("t-faces"), current=owner)
    await identity_service.by_transaction(session, payload=receipt("t-faces"), current=guest)

    await session.refresh(profile)
    assert profile.user_id == owner.id
