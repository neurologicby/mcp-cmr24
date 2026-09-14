"""Request-local authentication and authorization context."""

from contextvars import ContextVar

authkey_var: ContextVar[str | None] = ContextVar("authkey", default=None)
caller_id_var: ContextVar[str] = ContextVar("caller_id", default="anonymous")
caller_scopes_var: ContextVar[frozenset[str]] = ContextVar(
    "caller_scopes", default=frozenset()
)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
