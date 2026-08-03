"""
Voucher engine — admin-generates codes that grant a user 1 month (or N days) of
the Voucher Pro plan. Voucher Pro mirrors Interview Pro's allowance but excludes
the ElevenLabs-powered live voice interview (F2F), per the client's brief.

Key flow:
1. Admin generates a code: generate_voucher(...)
2. Admin shares the code with a selected user (email, WhatsApp, etc.).
3. User redeems: redeem_voucher(code, email) → activates purchase_plan with
   the voucher's plan + duration. The plan gates F2F via plan_config["f2f"].
"""
import os
import secrets
import string

from database import (
    create_voucher,
    get_voucher,
    list_vouchers,
    list_voucher_redemptions,
    redeem_voucher_atomic,
    revoke_voucher,
)
from credit_engine import purchase_plan


VOUCHER_CODE_PREFIX = "CV"
VOUCHER_CODE_ALPHABET = string.ascii_uppercase + string.digits
VOUCHER_CODE_SEGMENT_LEN = 4
VOUCHER_CODE_SEGMENTS = 2  # CV-XXXX-XXXX

DEFAULT_PLAN = "Voucher Pro"
DEFAULT_DURATION_DAYS = 30
DEFAULT_MAX_REDEMPTIONS = 1


def is_admin(email):
    """Admin gate driven by the ADMIN_EMAILS env var (comma-separated)."""
    if not email:
        return False
    raw = os.getenv("ADMIN_EMAILS", "") or ""
    admins = {e.strip().lower() for e in raw.split(",") if e.strip()}
    return email.strip().lower() in admins


def _generate_code():
    """Generate a human-shareable code like CV-A8K2-7M4Q."""
    segments = [
        "".join(secrets.choice(VOUCHER_CODE_ALPHABET) for _ in range(VOUCHER_CODE_SEGMENT_LEN))
        for _ in range(VOUCHER_CODE_SEGMENTS)
    ]
    return f"{VOUCHER_CODE_PREFIX}-" + "-".join(segments)


def generate_voucher(plan=DEFAULT_PLAN,
                     duration_days=DEFAULT_DURATION_DAYS,
                     max_redemptions=DEFAULT_MAX_REDEMPTIONS,
                     expires_at=None,
                     created_by=None,
                     note=None):
    """Create a new voucher and return its details. Retries on code collisions."""
    for _ in range(5):
        code = _generate_code()
        try:
            create_voucher(code, plan, int(duration_days), int(max_redemptions),
                           expires_at, created_by, note)
            return {
                "code": code,
                "plan": plan,
                "duration_days": int(duration_days),
                "max_redemptions": int(max_redemptions),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "status": "active",
                "created_by": created_by,
                "note": note,
            }
        except Exception:
            continue
    raise RuntimeError("Failed to generate a unique voucher code after 5 attempts")


def redeem_voucher(code, email):
    """Redeem a voucher for the given user.

    Validates atomically, then activates the plan via purchase_plan with the
    voucher's duration. Returns {"ok": True, ...} or {"ok": False, "reason"}.
    """
    code = (code or "").strip().upper()
    email = (email or "").strip().lower()
    if not code or not email:
        return {"ok": False, "reason": "missing_code_or_email"}

    voucher = redeem_voucher_atomic(code, email)
    if "error" in voucher:
        return {"ok": False, "reason": voucher["error"]}

    res = purchase_plan(
        "individual",
        email,
        voucher["plan"],
        stripe_session_id=None,
        duration_days=int(voucher["duration_days"]),
    )
    if not res.get("ok"):
        return {"ok": False, "reason": "plan_activation_failed"}
    return {
        "ok": True,
        "plan": res["plan"],
        "credits": res["credits"],
        "duration_days": int(voucher["duration_days"]),
    }


__all__ = [
    "DEFAULT_PLAN", "DEFAULT_DURATION_DAYS", "DEFAULT_MAX_REDEMPTIONS",
    "is_admin",
    "generate_voucher",
    "redeem_voucher",
    "get_voucher",
    "list_vouchers",
    "list_voucher_redemptions",
    "revoke_voucher",
]
