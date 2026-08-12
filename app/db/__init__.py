"""PostgreSQL layer.

Everything that must survive a restart lives here: users, wallet, subscriptions,
generations, chats, catalogue. Redis stays for what it is good at — sessions,
cache, rate limits, locks — and is no longer a system of record.

Routers never touch this package directly; they go through the repositories so
queries stay in one place instead of spreading across request handlers.
"""

from app.db.session import connect, disconnect, get_session, session_scope

__all__ = ["connect", "disconnect", "get_session", "session_scope"]
