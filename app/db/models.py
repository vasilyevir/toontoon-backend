"""Database tables.

Design notes that are easy to lose later:

* **Ids are prefixed strings** (``usr_``, ``gen_``…), not raw UUIDs, matching
  ``core.security.new_id`` and everything already stored in Redis. They show up
  in logs and share links, and a readable prefix is worth the extra bytes.
* **Enum-ish columns are plain text**, not native PG enums. The lists here grow
  (a new generation operation, a new subscription state), and altering a native
  enum in a migration is far more painful than a check-free text column.
* **The wallet is append-only.** ``wallet_ledger`` is only ever inserted into;
  ``wallet_balances`` is a materialised view of it kept in the same transaction.
  Any balance can be re-derived by summing the ledger.
* **Generations are soft-deleted.** A row referenced by "3 tokens spent" cannot
  simply vanish, so "delete" hides the work, drops the file and kills the share
  link while the bookkeeping stays intact.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.security import new_id

# Predictable constraint names — Alembic autogenerate produces unusable names
# without this, and dropping an unnamed constraint later is guesswork.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _id(prefix: str):
    return mapped_column(String(48), primary_key=True, default=lambda: new_id(prefix))


_now = func.now()


# ─── Identity ────────────────────────────────────────────────────────────────


class User(Base):
    """A guest is a row here too, with ``kind='guest'``.

    Keeping guests in the same table is the whole point: wallet, history and
    chat work for them without a single branch in business logic.
    """

    __tablename__ = "users"

    id: Mapped[str] = _id("usr_")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="guest")  # guest | registered
    email: Mapped[Optional[str]] = mapped_column(String(320), unique=True)  # stored lowercased
    password_hash: Mapped[Optional[str]] = mapped_column(Text)  # argon2, email sign-in only
    name: Mapped[Optional[str]] = mapped_column(String(120))
    avatar_key: Mapped[Optional[str]] = mapped_column(Text)  # object key, never a URL

    # Chat context boundary set by "Clear": messages older than this are still
    # shown to the user but not sent to the model (CH-20).
    chat_context_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    # Set when a guest is merged into an account: the guest row stays as a trail
    # so a disputed merge can be reconstructed.
    merged_into_user_id: Mapped[Optional[str]] = mapped_column(ForeignKey("users.id"))

    __table_args__ = (
        # Abandoned-guest cleanup scans by kind + last activity.
        Index("ix_users_kind_last_seen", "kind", "last_seen_at"),
    )


class AuthIdentity(Base):
    """One row per sign-in method, so one account can carry Apple + Google + email."""

    __tablename__ = "auth_identities"

    id: Mapped[str] = _id("aid_")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # email | apple | google
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_auth_identities_provider_external"),
        Index("ix_auth_identities_user", "user_id"),
    )


class UserPreferences(Base):
    """Answers from the onboarding quiz (CH-12).

    Collected from a guest, moved to the account on merge. ``onboarding_version``
    records which set of questions produced these answers — the six directions
    will change, and without it old answers become unreadable.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    gender: Mapped[Optional[str]] = mapped_column(String(16))  # female | male | other
    liked_categories: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(40)))
    disliked_categories: Mapped[Optional[list[str]]] = mapped_column(ARRAY(String(40)))
    onboarding_version: Mapped[Optional[str]] = mapped_column(String(32))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


# ─── Wallet ──────────────────────────────────────────────────────────────────


class WalletBalance(Base):
    """Two buckets, not one number (CH-17).

    ``sub_balance`` is reset to the plan maximum every 7 days from the purchase
    date and does not accumulate; ``free_balance`` accumulates up to ``free_cap``
    and never burns. Spending drains ``sub_balance`` first.
    """

    __tablename__ = "wallet_balances"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    free_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sub_balance: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sub_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sub_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    free_cap: Mapped[Optional[int]] = mapped_column(Integer)  # value comes with pricing
    last_reward_date: Mapped[Optional[date]] = mapped_column(Date)  # server date, never the device's
    reward_streak: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=_now, onupdate=_now, nullable=False
    )


class WalletLedger(Base):
    """Append-only journal. Never updated, never deleted.

    ``idempotency_key`` is what makes a repeat harmless: App Store delivers the
    same notification more than once and networks retry, so "apply again" has to
    be a no-op rather than a second grant.
    """

    __tablename__ = "wallet_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    bucket: Mapped[str] = mapped_column(String(8), nullable=False)  # free | sub
    delta: Mapped[int] = mapped_column(Integer, nullable=False)  # + grant, − charge
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    # signup | daily_reward | sub_grant | sub_reset | generation | refund | merge
    ref_id: Mapped[Optional[str]] = mapped_column(String(64))  # generation id, transaction id…
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)

    __table_args__ = (Index("ix_wallet_ledger_user_created", "user_id", "created_at"),)


class SubscriptionPlan(Base):
    """Tariffs, in the database rather than in code.

    Pricing changes far more often than the code that applies it, and every
    change would otherwise cost a release. ``weekly_quota`` is what the
    subscription bucket is reset **to** every 7 days from the purchase date —
    including on plans billed monthly or yearly, where several resets happen
    inside one billing period.
    """

    __tablename__ = "subscription_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 'weekly'
    product_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    # week | month | year — the App Store billing period, not the quota period.
    billing_period: Mapped[str] = mapped_column(String(16), nullable=False)
    price_usd: Mapped[Optional[int]] = mapped_column(Integer)  # cents, reference only
    weekly_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)


class Subscription(Base):
    """``original_transaction_id`` is unique globally — that is what stops one
    purchase from feeding two accounts."""

    __tablename__ = "subscriptions"

    id: Mapped[str] = _id("sub_")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    original_transaction_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    product_id: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[Optional[str]] = mapped_column(ForeignKey("subscription_plans.id"))
    # Anchor for the weekly quota reset: 7 days from purchase, independent of
    # App Store renewals (CH-17).
    quota_anchor_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # active | grace | expired | refunded
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    environment: Mapped[Optional[str]] = mapped_column(String(16))  # sandbox | production
    last_notification_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)

    __table_args__ = (Index("ix_subscriptions_user", "user_id"),)


class AppStoreNotification(Base):
    """Notification log that doubles as the dedup table.

    Apple retries delivery; with the notification UUID as the primary key, a
    repeat is a duplicate-key insert instead of a second grant. No special code.
    """

    __tablename__ = "appstore_notifications"

    notification_uuid: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[Optional[str]] = mapped_column(String(48))
    subtype: Mapped[Optional[str]] = mapped_column(String(48))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSONB)


# ─── Catalogue ───────────────────────────────────────────────────────────────


class Style(Base):
    """A style promises a result; it does not know which provider delivers it.

    ``operation`` says what to do, ``input_spec`` says what is needed to do it
    (prompt, source image, mask, parameters). The launch screen in the app is
    built from ``input_spec``, which is what lets a new operation appear without
    a new screen (CH-21).
    """

    __tablename__ = "styles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # 'cartoon_me_pixar'
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    # One of the six onboarding directions (CH-12/CH-14).
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    # text_to_image | image_to_image | image_to_video | inpaint | outpaint | upscale
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    input_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # tokens; per style
    prompt_template: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    examples: Mapped[Optional[dict]] = mapped_column(JSONB)  # object keys of sample results
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # manual curation
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_shot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Written from day one even though ordering is manual: when automatic
    # ranking is wanted, the history will already be there.
    usage_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)

    __table_args__ = (
        Index("ix_styles_home", "is_home", "sort_order"),
        Index("ix_styles_category", "category", "sort_order"),
    )


class ShotsSchedule(Base):
    """Optional manual override of the daily set.

    Without a row the set for a date is computed deterministically from the pool
    (styles with ``is_shot``) by UTC date, so every user sees the same "today".
    """

    __tablename__ = "shots_schedule"

    day: Mapped[date] = mapped_column(Date, primary_key=True)  # UTC
    style_ids: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False)


class StyleFavorite(Base):
    """Сохранённый стиль — закладка человека в каталоге.

    Хранится за пользователем, а не за устройством: закладку ставят, чтобы
    вернуться, и «вернуться» на другом телефоне — такой же законный случай.
    До входа приложение держит закладки у себя и при первом входе присылает их
    сюда: с гостевой сессией они бы потерялись при переустановке.

    Слияние поэтому только добавляет. Человек, ставивший закладки на двух
    устройствах, ожидает увидеть объединение, а не то, что последний вход
    стёр предыдущий.
    """

    __tablename__ = "style_favorites"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Без внешнего ключа на styles: каталог переезжает файлами, стиль могут
    # временно убрать и вернуть, и закладка не должна исчезать вместе с ним.
    # Пропавший стиль просто не отдаётся в списке.
    style_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_style_favorites_user", "user_id", "created_at"),)


class GenerationProvider(Base):
    """Registry of models. The adapter lives in code, the switch lives here.

    Enabling, priority, emergency fallback and A/B between two models become
    data rather than a release (CH-21).
    """

    __tablename__ = "generation_providers"

    id: Mapped[str] = mapped_column(String(48), primary_key=True)  # 'openai_images'
    operations: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    limits: Mapped[Optional[dict]] = mapped_column(JSONB)  # input size, formats, duration
    cost_hint: Mapped[Optional[dict]] = mapped_column(JSONB)  # money and latency ballpark
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)


# ─── Media and generations ───────────────────────────────────────────────────


class MediaAsset(Base):
    """One row per file: uploads, results and inpaint masks alike.

    Uploads and results share a table because the future reference picker shows
    them mixed, sorted by date — two tables would mean two queries and manual
    merging (CH-18).
    """

    __tablename__ = "media_assets"

    id: Mapped[str] = _id("med_")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # upload | generation | mask
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)  # object key, never a URL
    thumb_key: Mapped[Optional[str]] = mapped_column(Text)
    mime: Mapped[Optional[str]] = mapped_column(String(64))
    bytes: Mapped[Optional[int]] = mapped_column(BigInteger)  # for a future library cap
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64))  # sha256, dedups re-uploads
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_media_user_created",
            "user_id",
            "created_at",
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "uq_media_user_upload_hash",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where=(kind == "upload") & deleted_at.is_(None),
        ),
    )


class Generation(Base):
    """A single piece of work.

    ``request_params`` stores what the request actually was — answers, style,
    parameters — because "repeat" from history cannot reproduce anything from
    the result alone. ``provider_id`` and ``provider_model`` record what actually
    made it, which is how model comparisons and complaint handling stay possible
    after the registry changes.
    """

    __tablename__ = "generations"

    id: Mapped[str] = _id("gen_")
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    style_id: Mapped[Optional[str]] = mapped_column(ForeignKey("styles.id"))
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_id: Mapped[Optional[str]] = mapped_column(ForeignKey("generation_providers.id"))
    provider_model: Mapped[Optional[str]] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    # queued | running | done | failed — every operation is a job, even the fast
    # ones, so a slow provider later cannot break the client contract.
    prompt: Mapped[Optional[str]] = mapped_column(Text)
    request_params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("media_assets.id"))
    mask_media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("media_assets.id"))
    result_media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("media_assets.id"))
    cost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    share_id: Mapped[Optional[str]] = mapped_column(String(48), unique=True)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_generations_user_created",
            "user_id",
            "created_at",
            postgresql_where=deleted_at.is_(None),
        ),
        Index(
            "ix_generations_user_op_created",
            "user_id",
            "operation",
            "created_at",
            postgresql_where=deleted_at.is_(None),
        ),
        # The video worker polls for unfinished jobs.
        Index("ix_generations_status", "status", postgresql_where=status.in_(["queued", "running"])),
    )


# ─── Chat ────────────────────────────────────────────────────────────────────


class ChatMessage(Base):
    """One continuous chat per user — there is no ``chats`` table (CH-20).

    "Clear" does not delete anything: it moves ``users.chat_context_started_at``,
    so the model forgets while the person can still scroll back.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant
    content: Mapped[Optional[str]] = mapped_column(Text)
    generation_id: Mapped[Optional[str]] = mapped_column(ForeignKey("generations.id"))
    # Приложенный снимок. Отдельным полем, а не ссылкой в тексте: `content`
    # уезжает модели как реплика человека, и `/api/media/med_…` она прочитала бы
    # как то, что он сказал.
    media_id: Mapped[Optional[str]] = mapped_column(ForeignKey("media_assets.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now, nullable=False)

    __table_args__ = (Index("ix_chat_messages_user_created", "user_id", "created_at"),)
