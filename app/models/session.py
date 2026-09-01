from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.models.user import AuthProvider


class Session(BaseModel):
    """Server-side session stored in Redis under ``session:{sid}``.

    The session id itself lives in the ``toontoon-session`` http-only cookie.
    """

    sid: str
    user_id: str
    provider: AuthProvider

    # Когда сессия выдана, в секундах эпохи. Сравнивается с
    # `users.sessions_valid_from`: смена пароля двигает ту отметку, и всё
    # выданное раньше перестаёт пускать.
    #
    # Ноль по умолчанию, и это важнее, чем выглядит. Сессии, лежащие в Redis
    # со времён до этой правки, поля не имеют — и с умолчанием «сейчас» они
    # при каждом чтении выглядели бы свежевыданными, то есть смена пароля их
    # бы не отзывала. Ноль означает «выдана раньше всего на свете»: такие
    # сессии отзовутся первой же сменой пароля, что и требуется.
    issued_at: float = 0.0

    # Boostify OAuth tokens (only present for provider == boostify).
    boostify_access_token: Optional[str] = None
    boostify_refresh_token: Optional[str] = None
    boostify_access_expires_at: Optional[float] = None  # epoch seconds
