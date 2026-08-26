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
_ROOT_PEM = (pathlib.Path(__file__).parent / "certs" / "apple_root_ca_g3.pem").read_bytes()


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


def verify_transaction(signed: str, *, now: datetime | None = None) -> dict:
    """Проверить подпись и вернуть тело транзакции.

    Бросает `BadTransaction` на любом сомнении. Промежуточных состояний здесь
    нет: чек либо доказан, либо не считается.
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
    if len(chain_raw) < 2:
        raise BadTransaction("в заголовке нет цепочки сертификатов")
    try:
        chain = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in chain_raw]
    except Exception as exc:  # noqa: BLE001
        raise BadTransaction("сертификаты не читаются") from exc

    root = x509.load_pem_x509_certificate(_ROOT_PEM)
    # Корень сверяем побайтно. «Тоже от Apple» и «этот самый» — разные вещи, и
    # вторая проверяется только так.
    der = serialization.Encoding.DER
    if chain[-1].public_bytes(encoding=der) != root.public_bytes(encoding=der):
        raise BadTransaction("цепочка ведёт не к нашему корню")

    for cert in chain:
        _check_dates(cert, now)
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
        payload = json.loads(_b64url(payload_raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise BadTransaction("тело не читается") from exc

    # Чужое приложение подписано тем же корнем. Без этой проверки покупка из
    # соседней программы открывала бы дверь сюда.
    wanted = (settings.apple_bundle_id or "").strip()
    if wanted and payload.get("bundleId") != wanted:
        raise BadTransaction(f"чек от другого приложения: {payload.get('bundleId')}")

    if not payload.get("originalTransactionId"):
        raise BadTransaction("в чеке нет originalTransactionId")
    return payload
