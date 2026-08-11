"""
Coupon engine — admin-generates codes that grant a fixed number of wallet
credits (top-up, no expiry) when redeemed by a user.

Distinct from `voucher_engine`, which activates a plan. Coupons are pure
token grants: the user keeps their current plan, just gets +N credits
on the `pack_credits` bucket.

Key flow:
1. Admin generates a code: generate_coupon(credits=..., max_redemptions=...)
2. Admin shares the code with a user.
3. User redeems: redeem_coupon(code, email) → grants `credits` to wallet
   via credit_engine.grant_credits (idempotent on code+email pair).
4. Admin can revoke the code from the admin panel at any time.
"""
import secrets
import string

from database import (
    create_coupon,
    get_coupon,
    list_coupons,
    list_coupon_redemptions,
    redeem_coupon_atomic,
    revoke_coupon,
)
from credit_engine import grant_credits
from voucher_engine import is_admin  # single admin gate


COUPON_CODE_PREFIX = "CP"
COUPON_CODE_ALPHABET = string.ascii_uppercase + string.digits
COUPON_CODE_SEGMENT_LEN = 4
COUPON_CODE_SEGMENTS = 2  # CP-XXXX-XXXX

DEFAULT_MAX_REDEMPTIONS = 1
MIN_CREDITS = 1
MAX_CREDITS = 10000


def _generate_code():
    """Generate a human-shareable code like CP-A8K2-7M4Q."""
    segments = [
        "".join(secrets.choice(COUPON_CODE_ALPHABET)
                for _ in range(COUPON_CODE_SEGMENT_LEN))
        for _ in range(COUPON_CODE_SEGMENTS)
    ]
    return f"{COUPON_CODE_PREFIX}-" + "-".join(segments)


def _idempotency_key(code: str, email: str) -> str:
    return f"coupon:{code.strip().upper()}:{email.strip().lower()}"


def generate_coupon(
    credits,
    max_redemptions=DEFAULT_MAX_REDEMPTIONS,
    expires_at=None,
    target_email=None,
    created_by=None,
    note=None,
):
    """Create a new coupon and return its details. Retries on code collisions.

    Args:
        credits: Positive integer (1..10000).
        max_redemptions: Integer >= 1.
        expires_at: Optional datetime.
        target_email: Optional; when set, only this user can redeem.
        created_by: Admin email (for audit).
        note: Optional free-text note.
    """
    credits = int(credits)
    if credits < MIN_CREDITS or credits > MAX_CREDITS:
        raise ValueError(f"credits must be between {MIN_CREDITS} and {MAX_CREDITS}")
    max_redemptions = int(max_redemptions)
    if max_redemptions < 1:
        raise ValueError("max_redemptions must be >= 1")
    target_email = (
        target_email.strip().lower() if target_email else None
    ) or None

    for _ in range(5):
        code = _generate_code()
        try:
            create_coupon(
                code,
                credits,
                max_redemptions,
                expires_at,
                target_email,
                created_by,
                note,
            )
            return {
                "code": code,
                "credits": credits,
                "max_redemptions": max_redemptions,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "target_email": target_email,
                "status": "active",
                "created_by": created_by,
                "note": note,
            }
        except Exception:
            continue
    raise RuntimeError("Failed to generate a unique coupon code after 5 attempts")


def redeem_coupon(code, email, account_type="individual"):
    """Redeem a coupon for the given user.

    Validates atomically (via database.redeem_coupon_atomic), then grants
    credits via credit_engine.grant_credits with an idempotency key of
    `coupon:<code>:<email>` so retries can't double-grant.

    Returns:
        {"ok": True, "code": ..., "credits": int, "balance_after": int,
         "duplicate": bool}
        {"ok": False, "reason": "wrong_recipient"|"not_found"|"inactive"|
                                "expired"|"max_redemptions"|"already_redeemed"|
                                "grant_failed"|"missing_code_or_email"}
    """
    code = (code or "").strip().upper()
    email = (email or "").strip().lower()
    if not code or not email:
        return {"ok": False, "reason": "missing_code_or_email"}

    coupon = redeem_coupon_atomic(code, email)
    if "error" in coupon:
        return {"ok": False, "reason": coupon["error"]}

    credits = int(coupon["credits"])
    grant = grant_credits(
        account_type,
        email,
        credits,
        feature=f"Coupon: {code}",
        source="gift",
        reference_code=code,
        idempotency_key=_idempotency_key(code, email),
    )
    if not grant.get("ok"):
        return {"ok": False, "reason": "grant_failed"}

    return {
        "ok": True,
        "code": code,
        "credits": credits,
        "balance_after": grant.get("balance_after", 0),
        "duplicate": bool(grant.get("duplicate")),
    }


__all__ = [
    "is_admin",
    "generate_coupon",
    "redeem_coupon",
    "revoke_coupon",
    "get_coupon",
    "list_coupons",
    "list_coupon_redemptions",
    "COUPON_CODE_PREFIX",
    "MIN_CREDITS",
    "MAX_CREDITS",
]
