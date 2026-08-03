import hashlib
import secrets

from database import (
    create_auth_session,
    delete_auth_session,
    get_auth_session,
    get_business_user,
    get_user_data,
    purge_expired_auth_sessions,
)

# Browser cookie name holding the raw (server-verifiable) session token.
SESSION_COOKIE_NAME = "cvolve_auth"
SESSION_DURATION_DAYS = 30


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_session(user_email, account_type="individual"):
    """Create a persisted session and return the raw cookie token.

    Only the sha256 hash is stored in the DB; the raw token lives in the user's
    cookie so it never touches our tables.
    """
    raw_token = secrets.token_urlsafe(32)
    try:
        create_auth_session(
            _hash_token(raw_token),
            user_email,
            account_type=account_type,
            duration_days=SESSION_DURATION_DAYS,
        )
        return raw_token
    except Exception:
        return None


def validate_session(raw_token):
    """Validate a cookie token and hydrate the user.

    Returns {"user": <user dict>, "account_type": <str>} or None.
    """
    if not raw_token:
        return None
    try:
        session = get_auth_session(_hash_token(raw_token))
        if not session:
            return None
        email = session.get("user_email")
        account_type = session.get("account_type") or "individual"
        if account_type == "business":
            user = get_business_user(email)
        else:
            user = get_user_data(email)
        if not user:
            return None
        return {"user": user, "account_type": account_type}
    except Exception:
        return None


def revoke_session(raw_token):
    """Invalidate a persisted session (logout everywhere for that token)."""
    if not raw_token:
        return
    try:
        delete_auth_session(_hash_token(raw_token))
    except Exception:
        pass


def purge_expired_sessions():
    """Best-effort cleanup of expired persisted sessions."""
    try:
        purge_expired_auth_sessions()
    except Exception:
        pass
