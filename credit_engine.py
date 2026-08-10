"""
Credit engine — single, atomic, account-type-agnostic source of truth for
credit balances, plan renewals, credit packs, free-plan counters, ATS hash-pair
dedup and F2F (live voice) block billing.

The legacy `users.credits` / `business_users.credits` columns are shadow values
kept for backward compatibility; the `credit_wallets` table is authoritative.
"""
import uuid
import hashlib
from datetime import datetime, timedelta

from database import (
    get_db_connection,
    release_db_connection,
    record_credit_usage,
)
from pricing import (
    PLANS,
    FREE_PLAN,
    PACKS,
    pack_config,
    plan_config,
    corporate_plan_config,
    credit_cost,
    CYCLE_DAYS,
    F2F_BLOCK_MINUTES,
    F2F_BLOCK_CREDITS,
    FREE_VOICE_INTERVIEW_MINUTES,
)


# ---------------------------------------------------------------------------
# Feature aliases → pricing key, and free-plan limits by legacy feature name
# ---------------------------------------------------------------------------
FEATURE_ALIASES = {
    "CV": "cv_tailor",
    "CV Create": "cv_create",
    "CV Tailor": "cv_tailor",
    "CV Translate": "cv_translate",
    "Cover Letter": "cl_create",
    "CL Create": "cl_create",
    "CL Translate": "cl_translate",
    "ATS": "ats",
    "Job Search": "job_search",
    "Job Match": "job_search",
    "Interview Practice": "interview_behavioral_qa",
    "Technical Interview": "interview_tech_qa",
    "Behavioral Interview": "interview_behavioral_qa",
    "Text Mock Interview": "interview_text_mock",
    "STAR Answer": "interview_star",
    "Improve Answer": "interview_improve_answer",
    "Q&A Bank Download": "qa_bank_download",
    "F2F Interview": "cv_tailor",  # F2F charges explicit block credits
}

# Feature strings that are limited for Free-plan users (values = pricing keys)
FREE_LIMITED_FEATURES = {
    "CV": "cv_limit",
    "Cover Letter": "cl_limit",
    "ATS": "ats_limit",
    "Job Search": "job_search_limit",
    "Job Match": "job_search_limit",
    "Voice Interview": "voice_interview_once",
}

TEST_DOMAIN_MARKERS = ("tester@cvolvepro.com", "test")


def is_test_account(email):
    """Legacy test-account bypass."""
    if not email:
        return False
    email = str(email).lower()
    return any(m in email for m in TEST_DOMAIN_MARKERS)


def _feature_cost(feature, amount=None):
    if amount is not None and amount > 0:
        return int(amount)
    return credit_cost(FEATURE_ALIASES.get(feature, feature))


# ---------------------------------------------------------------------------
# Plan helpers (no payment.py import → keeps engine decoupled/testable)
# ---------------------------------------------------------------------------
def get_plan_name(account_type, email):
    """Return the current plan name (defaults to 'Free')."""
    if is_test_account(email):
        return "Interview Pro"
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                return _plan_name_locked(cur, account_type, email)
        finally:
            release_db_connection(conn)
    except Exception:
        return "Free"


def _plan_name_locked(cur, account_type, email):
    if account_type == "business":
        cur.execute("SELECT current_plan FROM business_users WHERE email=%s", (email.lower(),))
        row = cur.fetchone()
        return row[0] if row and row[0] else "Free"
    cur.execute(
        """
        SELECT plan FROM subscriptions
         WHERE user_email=%s AND status='active' AND end_date > CURRENT_TIMESTAMP
         ORDER BY end_date DESC LIMIT 1
        """,
        (email,),
    )
    row = cur.fetchone()
    return row[0] if row else "Free"


# ---------------------------------------------------------------------------
# Wallet lifecycle
# ---------------------------------------------------------------------------
def _ensure_wallet(cur, account_type, email):
    cur.execute(
        "SELECT id FROM credit_wallets WHERE account_type=%s AND email=%s FOR UPDATE",
        (account_type, email),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    # Seed from legacy shadow columns so existing balances carry over.
    plan = _plan_name_locked(cur, account_type, email)
    if account_type == "business":
        cur.execute(
            "SELECT COALESCE(credits, 0) FROM business_users WHERE email=%s",
            (email.lower(),),
        )
    else:
        cur.execute("SELECT COALESCE(credits, 0) FROM users WHERE email=%s", (email,))
    leg = cur.fetchone()
    legacy = int(leg[0]) if leg else 0

    cur.execute(
        """
        INSERT INTO credit_wallets
            (account_type, email, plan, subscription_credits, cycle_start, next_renewal)
        VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + %s * INTERVAL '1 day')
        ON CONFLICT (account_type, email) DO NOTHING
        """,
        (account_type, email, plan, legacy, CYCLE_DAYS),
    )
    cur.execute(
        "SELECT id FROM credit_wallets WHERE account_type=%s AND email=%s",
        (account_type, email),
    )
    return cur.fetchone()[0]


def _purge_expired_packs_locked(cur, account_type, email):
    cur.execute(
        """
        SELECT id, credits_remaining FROM credit_packs
         WHERE account_type=%s AND email=%s AND expires_at <= CURRENT_TIMESTAMP
         FOR UPDATE
        """,
        (account_type, email),
    )
    rows = cur.fetchall()
    if not rows:
        return
    total = sum(int(r[1]) for r in rows)
    for pid, _ in rows:
        cur.execute("DELETE FROM credit_packs WHERE id=%s", (pid,))
    if total:
        cur.execute(
            """
            UPDATE credit_wallets
               SET pack_credits = GREATEST(0, pack_credits - %s),
                   updated_at = CURRENT_TIMESTAMP
             WHERE account_type=%s AND email=%s
            """,
            (total, account_type, email),
        )
        cur.execute(
            """
            INSERT INTO credit_transactions
                (account_type, email, feature, amount, txn_type, source, balance_after, request_id)
            VALUES (%s, %s, 'Pack Expiry', %s, 'expire', 'pack', 0, %s)
            """,
            (account_type, email, -total, str(uuid.uuid4())),
        )


def _plan_allowance_and_days(plan, account_type):
    """Allowance credits + renewal window for a plan, per account type."""
    if account_type == "business":
        corp = corporate_plan_config(plan)
        if corp:
            return corp["credits"], corp["duration_days"]
        return 0, CYCLE_DAYS
    cfg = plan_config(plan)
    return (cfg["monthly_credits"] if cfg else 0), CYCLE_DAYS


def _renew_and_purge_locked(cur, account_type, email):
    """Monthly/periodic renewal (expire credits → top up to plan allowance) + pack purge."""
    _purge_expired_packs_locked(cur, account_type, email)
    wallet_id = _ensure_wallet(cur, account_type, email)
    cur.execute(
        "SELECT plan FROM credit_wallets WHERE id=%s FOR UPDATE",
        (wallet_id,),
    )
    plan = cur.fetchone()[0]
    allowance, days = _plan_allowance_and_days(plan, account_type)

    cur.execute(
        """
        SELECT (next_renewal IS NULL OR next_renewal <= CURRENT_TIMESTAMP)
          FROM credit_wallets WHERE id=%s
        """,
        (wallet_id,),
    )
    due = cur.fetchone()[0]

    if due:
        cur.execute(
            """
            UPDATE credit_wallets
               SET subscription_credits=%s,
                   cycle_start=CURRENT_TIMESTAMP,
                   next_renewal=CURRENT_TIMESTAMP + %s * INTERVAL '1 day',
                   updated_at=CURRENT_TIMESTAMP
             WHERE id=%s
            """,
            (allowance, days, wallet_id),
        )
        cur.execute(
            """
            INSERT INTO credit_transactions
                (account_type, email, feature, amount, txn_type, source, balance_after, request_id)
            VALUES (%s, %s, 'Monthly Renewal', %s, 'renewal', 'subscription', %s, %s)
            """,
            (account_type, email, allowance, allowance, str(uuid.uuid4())),
        )
    return wallet_id


def renew_if_due(account_type, email):
    """Public entry: renew + purge in its own transaction."""
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                _renew_and_purge_locked(cur, account_type, email)
        finally:
            release_db_connection(conn)
        return True
    except Exception:
        return False


def wallet_balance(account_type, email):
    """Return a summary dict of the wallet for display / gating."""
    if is_test_account(email):
        return {
            "ok": True,
            "account_type": account_type,
            "email": email,
            "plan": "Interview Pro",
            "is_free": False,
            "subscription_credits": 10_000_000,
            "pack_credits": 0,
            "total": 10_000_000,
            "cycle_end": "2099-12-31",
            "pack_expiry": None,
            "packs": [],
        }
    email = email.strip().lower()
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            _renew_and_purge_locked(cur, account_type, email)
            cur.execute(
                """
                SELECT plan, subscription_credits, pack_credits, next_renewal
                  FROM credit_wallets WHERE account_type=%s AND email=%s
                """,
                (account_type, email),
            )
            row = cur.fetchone()
            if not row:
                return {
                    "ok": True, "account_type": account_type, "email": email,
                    "plan": "Free", "is_free": True, "subscription_credits": 0,
                    "pack_credits": 0, "total": 0, "cycle_end": None,
                    "pack_expiry": None, "packs": [],
                }
            plan, sub, pack, next_renewal = row
            cur.execute(
                """
                SELECT id, pack_name, credits_remaining, expires_at
                  FROM credit_packs
                 WHERE account_type=%s AND email=%s AND credits_remaining > 0
                       AND expires_at > CURRENT_TIMESTAMP
                 ORDER BY expires_at ASC
                """,
                (account_type, email),
            )
            packs = [
                {
                    "id": p[0], "name": p[1], "credits_remaining": int(p[2]),
                    "expires_at": p[3].isoformat() if p[3] else None,
                }
                for p in cur.fetchall()
            ]
            pack_expiry = packs[0]["expires_at"] if packs else None
            plan_name = plan if plan else "Free"
            cfg = plan_config(plan_name)
            is_free = (account_type == "individual" and plan_name == "Free")
            result = {
                "ok": True,
                "account_type": account_type,
                "email": email,
                "plan": plan_name,
                "is_free": is_free,
                "subscription_credits": int(sub or 0),
                "pack_credits": int(pack or 0),
                "total": int(sub or 0) + int(pack or 0),
                "cycle_end": next_renewal.isoformat() if next_renewal else None,
                "pack_expiry": pack_expiry,
                "packs": packs,
                "f2f_enabled": bool(cfg and cfg.get("f2f")),
                "f2f_max_minutes": (cfg or {}).get("f2f_max_minutes", 0),
            }
            if is_free:
                result["free_counters"] = free_usage_summary(account_type, email)
            return result
    finally:
        release_db_connection(conn)


# ---------------------------------------------------------------------------
# Charge / refund
# ---------------------------------------------------------------------------
def charge(account_type, email, amount, feature=None, idempotency_key=None):
    """
    Atomically charge `amount` credits. Subscription credits are consumed first,
    then credit packs FIFO (oldest expiry first). Returns a result dict.
    """
    email = email.strip().lower()
    if is_test_account(email):
        if feature:
            try:
                record_credit_usage(email, feature, 0)
            except Exception:
                pass
        return {"ok": True, "charged": 0, "subscription_charged": 0, "pack_charged": 0}

    amount = int(amount)
    if amount <= 0:
        return {"ok": True, "charged": 0, "subscription_charged": 0, "pack_charged": 0}

    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            wallet_id = _renew_and_purge_locked(cur, account_type, email)

            if idempotency_key:
                cur.execute(
                    """
                    SELECT 1 FROM credit_transactions
                     WHERE account_type=%s AND email=%s AND idempotency_key=%s LIMIT 1
                    """,
                    (account_type, email, idempotency_key),
                )
                if cur.fetchone():
                    return {"ok": True, "charged": amount, "already_processed": True}

            cur.execute(
                "SELECT subscription_credits, pack_credits FROM credit_wallets WHERE id=%s FOR UPDATE",
                (wallet_id,),
            )
            sub, pack = cur.fetchone()
            sub, pack = int(sub or 0), int(pack or 0)

            remaining = amount
            take_sub = min(sub, remaining)
            remaining -= take_sub

            pack_charges = []  # (pack_id, amount)
            if remaining > 0 and pack > 0:
                cur.execute(
                    """
                    SELECT id, credits_remaining FROM credit_packs
                     WHERE account_type=%s AND email=%s AND credits_remaining > 0
                           AND expires_at > CURRENT_TIMESTAMP
                     ORDER BY expires_at ASC FOR UPDATE
                    """,
                    (account_type, email),
                )
                for pid, prem in cur.fetchall():
                    if remaining <= 0:
                        break
                    d = min(int(prem), remaining)
                    remaining -= d
                    pack_charges.append((pid, d))
                    cur.execute(
                        "UPDATE credit_packs SET credits_remaining = credits_remaining - %s WHERE id=%s",
                        (d, pid),
                    )

            if remaining > 0:
                return {"ok": False, "reason": "insufficient", "charged": 0}

            new_sub = sub - take_sub
            new_pack = pack - sum(d for _, d in pack_charges)
            cur.execute(
                """
                UPDATE credit_wallets
                   SET subscription_credits=%s, pack_credits=%s, updated_at=CURRENT_TIMESTAMP
                 WHERE id=%s
                """,
                (new_sub, new_pack, wallet_id),
            )
            balance_after = new_sub + new_pack

            group_id = str(uuid.uuid4())
            first = True
            if take_sub > 0:
                cur.execute(
                    """
                    INSERT INTO credit_transactions
                        (account_type, email, feature, amount, txn_type, source,
                         balance_after, request_id, idempotency_key, group_id)
                    VALUES (%s, %s, %s, %s, 'charge', 'subscription', %s, %s, %s, %s)
                    """,
                    (account_type, email, feature, -take_sub, balance_after,
                     str(uuid.uuid4()), idempotency_key if first else None, group_id),
                )
                first = False
            for pid, d in pack_charges:
                cur.execute(
                    """
                    INSERT INTO credit_transactions
                        (account_type, email, feature, amount, txn_type, source,
                         pack_id, balance_after, request_id, idempotency_key, group_id)
                    VALUES (%s, %s, %s, %s, 'charge', 'pack', %s, %s, %s, %s, %s)
                    """,
                    (account_type, email, feature, -d, pid, balance_after,
                     str(uuid.uuid4()), idempotency_key if first else None, group_id),
                )
                first = False
            if feature:
                try:
                    record_credit_usage(email, feature, amount)
                except Exception:
                    pass
        return {
            "ok": True,
            "charged": amount,
            "subscription_charged": take_sub,
            "pack_charged": sum(d for _, d in pack_charges),
        }
    finally:
        release_db_connection(conn)


def _credit_back_pack(cur, account_type, email, pack_id, amount):
    """Credit `amount` back to a pack (or aggregate if the pack has expired)."""
    credited_row = False
    if pack_id:
        cur.execute(
            """
            UPDATE credit_packs SET credits_remaining = credits_remaining + %s
             WHERE id=%s AND expires_at > CURRENT_TIMESTAMP
            """,
            (amount, pack_id),
        )
        credited_row = cur.rowcount > 0
    cur.execute(
        """
        UPDATE credit_wallets SET pack_credits = pack_credits + %s
         WHERE account_type=%s AND email=%s
        """,
        (amount, account_type, email),
    )
    return credited_row


def refund(account_type, email, amount, feature=None, reference_txn_id=None, idempotency_key=None):
    """
    Refund credits, crediting back to the same source(s) as the original charge
    (when reference_txn_id is provided). Returns {'ok': bool, 'refunded': int}.
    """
    email = email.strip().lower()
    amount = int(amount)
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            wallet_id = _renew_and_purge_locked(cur, account_type, email)

            if idempotency_key:
                cur.execute(
                    """
                    SELECT 1 FROM credit_transactions
                     WHERE account_type=%s AND email=%s AND idempotency_key=%s LIMIT 1
                    """,
                    (account_type, email, idempotency_key),
                )
                if cur.fetchone():
                    return {"ok": True, "refunded": 0, "already_processed": True}

            sources = []  # (source, pack_id, amount)
            if reference_txn_id:
                cur.execute(
                    """
                    SELECT group_id FROM credit_transactions
                     WHERE id=%s AND account_type=%s AND email=%s
                    """,
                    (reference_txn_id, account_type, email),
                )
                root = cur.fetchone()
                if root and root[0]:
                    cur.execute(
                        """
                        SELECT id FROM credit_transactions
                         WHERE group_id=%s AND account_type=%s AND email=%s AND txn_type='charge'
                        """,
                        (root[0], account_type, email),
                    )
                    for (tid,) in cur.fetchall():
                        cur.execute(
                            "SELECT source, pack_id, amount FROM credit_transactions WHERE id=%s",
                            (tid,),
                        )
                        src, pid, amt = cur.fetchone()
                        if src == "subscription":
                            sources.append(("subscription", None, -int(amt)))
                        elif src == "pack":
                            sources.append(("pack", pid, -int(amt)))
            if not sources:
                sources = [("subscription", None, amount)]

            remaining = amount
            refunded = 0
            for src, pid, avail in sources:
                if remaining <= 0:
                    break
                d = min(avail, remaining)
                remaining -= d
                refunded += d
                if src == "subscription":
                    cur.execute(
                        "UPDATE credit_wallets SET subscription_credits = subscription_credits + %s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
                        (d, wallet_id),
                    )
                else:
                    _credit_back_pack(cur, account_type, email, pid, d)

            if refunded == 0:
                return {"ok": False, "reason": "nothing_to_refund", "refunded": 0}

            cur.execute(
                "SELECT subscription_credits, pack_credits FROM credit_wallets WHERE id=%s",
                (wallet_id,),
            )
            sub, pack = cur.fetchone()
            balance_after = int(sub or 0) + int(pack or 0)
            cur.execute(
                """
                INSERT INTO credit_transactions
                    (account_type, email, feature, amount, txn_type, source,
                     balance_after, request_id, idempotency_key, reference_txn_id)
                VALUES (%s, %s, %s, %s, 'refund', 'subscription', %s, %s, %s, %s)
                """,
                (account_type, email, feature, refunded, balance_after,
                 str(uuid.uuid4()), idempotency_key, reference_txn_id),
            )
        return {"ok": True, "refunded": refunded}
    finally:
        release_db_connection(conn)


# ---------------------------------------------------------------------------
# Unified spend helper (free-plan counters + paid charging)
# ---------------------------------------------------------------------------
def has_enough(account_type, email, amount=None, feature=None):
    """Pre-check (cost preview before execution)."""
    if is_test_account(email):
        return True
    email = email.strip().lower()
    plan = get_plan_name(account_type, email)
    if account_type == "individual" and plan == "Free":
        limit_key = FREE_LIMITED_FEATURES.get(feature)
        if limit_key is not None:
            return free_usage_available(account_type, email, feature)
    cost = _feature_cost(feature, amount)
    bal = wallet_balance(account_type, email)
    return bal.get("total", 0) >= cost


def spend_credits(account_type, email, feature, amount=None, idempotency_key=None):
    """
    Spend credits for a feature. On the Free plan, limited features consume the
    free counter instead of wallet credits. Returns a result dict.
    """
    email = email.strip().lower()
    if is_test_account(email):
        if feature:
            try:
                record_credit_usage(email, feature, 0)
            except Exception:
                pass
        return {"ok": True, "charged": 0, "free_used": False}

    plan = get_plan_name(account_type, email)
    if account_type == "individual" and plan == "Free":
        limit_key = FREE_LIMITED_FEATURES.get(feature)
        if limit_key is not None:
            if free_usage_available(account_type, email, feature):
                increment_free_usage(account_type, email, feature)
                try:
                    record_credit_usage(email, feature, 0)
                except Exception:
                    pass
                return {"ok": True, "charged": 0, "free_used": True}
            return {"ok": False, "reason": "free_limit", "charged": 0, "free_used": False}

    cost = _feature_cost(feature, amount)
    return charge(account_type, email, cost, feature=feature, idempotency_key=idempotency_key)


# --------------------------------------------------------------------# ---------------------------------------------------------------------------
# Free-plan counters
# ---------------------------------------------------------------------------
LIFETIME_FEATURES = {"Voice Interview"}

def _period_start(feature=None):
    if feature in LIFETIME_FEATURES:
        return datetime(1970, 1, 1)
    # Calendar-month period key for counter reset.
    return datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def free_usage_summary(account_type, email):
    """Return {feature: {used, limit}} for Free-plan users."""
    limits = {
        "CV": FREE_PLAN["cv_limit"],
        "Cover Letter": FREE_PLAN["cl_limit"],
        "ATS": FREE_PLAN["ats_limit"],
        "Job Search": FREE_PLAN["job_search_limit"],
        "Job Match": FREE_PLAN["job_search_limit"],
        "Voice Interview": 1 if FREE_PLAN["voice_interview_once"] else 0,
    }
    used = {}
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT feature, count FROM free_usage_counters
                     WHERE account_type=%s AND email=%s AND (period_start=%s OR period_start=%s)
                    """,
                    (account_type, email, _period_start(), _period_start("Voice Interview")),
                )
                used = {f: int(c) for f, c in cur.fetchall()}
        finally:
            release_db_connection(conn)
    except Exception:
        used = {}
    out = {}
    for feature, limit in limits.items():
        out[feature] = {"used": used.get(feature, 0), "limit": limit}
    return out


def free_usage_available(account_type, email, feature):
    limits = {
        "CV": FREE_PLAN["cv_limit"],
        "Cover Letter": FREE_PLAN["cl_limit"],
        "ATS": FREE_PLAN["ats_limit"],
        "Job Search": FREE_PLAN["job_search_limit"],
        "Job Match": FREE_PLAN["job_search_limit"],
        "Voice Interview": 1 if FREE_PLAN["voice_interview_once"] else 0,
    }
    limit = limits.get(feature)
    if limit is None:
        return False
    return free_usage_summary(account_type, email).get(feature, {}).get("used", 0) < limit


def increment_free_usage(account_type, email, feature):
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                p_start = _period_start(feature)
                cur.execute(
                    """
                    INSERT INTO free_usage_counters (account_type, email, feature, count, period_start)
                    VALUES (%s, %s, %s, 1, %s)
                    ON CONFLICT (account_type, email, feature, period_start)
                    DO UPDATE SET count = free_usage_counters.count + 1
                    """,
                    (account_type, email, feature, p_start),
                )
        finally:
            release_db_connection(conn)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------
def purchase_plan(account_type, email, plan_name, stripe_session_id=None, duration_days=None):
    """Activate a plan: overwrite credits with the new allowance, start a fresh cycle."""
    email = email.strip().lower()
    allowance, days = _plan_allowance_and_days(plan_name, account_type)
    if duration_days:
        days = int(duration_days)

    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            wallet_id = _purge_and_ensure(cur, account_type, email)
            cur.execute(
                """
                UPDATE credit_wallets
                   SET plan=%s, subscription_credits=%s, cycle_start=CURRENT_TIMESTAMP,
                       next_renewal=CURRENT_TIMESTAMP + %s * INTERVAL '1 day',
                       updated_at=CURRENT_TIMESTAMP
                  WHERE id=%s
                """,
                (plan_name, allowance, days, wallet_id),
            )
            cur.execute(
                """
                INSERT INTO credit_transactions
                    (account_type, email, feature, amount, txn_type, source, balance_after, request_id)
                VALUES (%s, %s, 'Plan Purchase', %s, 'plan_purchase', 'subscription', %s, %s)
                """,
                (account_type, email, allowance, allowance, str(uuid.uuid4())),
            )
            # Legacy tables kept in sync for backward compat.
            if account_type == "business":
                cur.execute(
                    """
                    UPDATE business_users
                       SET current_plan=%s,
                           credits=%s,
                           plan_expiry=CURRENT_TIMESTAMP + %s * INTERVAL '1 day'
                      WHERE email=%s
                    """,
                    (plan_name, allowance, days, email),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO subscriptions (user_email, plan, status, start_date, end_date, stripe_subscription_id)
                    VALUES (%s, %s, 'active', CURRENT_TIMESTAMP,
                            CURRENT_TIMESTAMP + %s * INTERVAL '1 day', %s)
                    """,
                    (email, plan_name, days, stripe_session_id),
                )
                cur.execute(
                    "UPDATE users SET credit_cycle_start = CURRENT_TIMESTAMP WHERE email=%s",
                    (email,),
                )
    finally:
        release_db_connection(conn)
    return {"ok": True, "plan": plan_name, "credits": allowance}


def _purge_and_ensure(cur, account_type, email):
    _purge_expired_packs_locked(cur, account_type, email)
    return _ensure_wallet(cur, account_type, email)


def purchase_pack(account_type, email, pack_name, stripe_session_id=None):
    """Add a credit pack (90-day validity) to the wallet."""
    email = email.strip().lower()
    cfg = pack_config(pack_name)
    if not cfg:
        return {"ok": False, "reason": "unknown_pack"}
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            wallet_id = _purge_and_ensure(cur, account_type, email)
            cur.execute(
                """
                INSERT INTO credit_packs
                    (account_type, email, pack_name, credits, credits_remaining, expires_at, stripe_session_id)
                VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP + %s * INTERVAL '1 day', %s)
                """,
                (account_type, email, pack_name, cfg["credits"], cfg["credits"],
                 cfg["valid_days"], stripe_session_id),
            )
            cur.execute(
                """
                UPDATE credit_wallets SET pack_credits = pack_credits + %s, updated_at=CURRENT_TIMESTAMP
                 WHERE id=%s
                """,
                (cfg["credits"], wallet_id),
            )
            cur.execute(
                """
                INSERT INTO credit_transactions
                    (account_type, email, feature, amount, txn_type, source, balance_after, request_id)
                VALUES (%s, %s, 'Pack Purchase', %s, 'pack_purchase', 'pack', %s, %s)
                """,
                (account_type, email, cfg["credits"], cfg["credits"], str(uuid.uuid4())),
            )
    finally:
        release_db_connection(conn)
    return {"ok": True, "pack": pack_name, "credits": cfg["credits"]}


def get_credit_packs(account_type, email):
    """Active packs for display."""
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT id, pack_name, credits, credits_remaining, expires_at, purchased_at
                      FROM credit_packs
                     WHERE account_type=%s AND email=%s AND credits_remaining > 0
                     ORDER BY expires_at ASC
                    """,
                    (account_type, email),
                )
                return [
                    {
                        "id": r[0], "name": r[1], "credits": int(r[2]),
                        "remaining": int(r[3]),
                        "expires_at": r[4].isoformat() if r[4] else None,
                        "purchased_at": r[5].isoformat() if r[5] else None,
                    }
                    for r in cur.fetchall()
                ]
        finally:
            release_db_connection(conn)
    except Exception:
        return []


def recent_transactions(account_type, email, limit=20):
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT created_at, feature, amount, txn_type, source, balance_after, id, reference_txn_id
                      FROM credit_transactions
                     WHERE account_type=%s AND email=%s
                     ORDER BY created_at DESC LIMIT %s
                    """,
                    (account_type, email, limit),
                )
                return [
                    {
                        "created_at": r[0].isoformat() if r[0] else None,
                        "feature": r[1], "amount": int(r[2]), "txn_type": r[3],
                        "source": r[4], "balance_after": int(r[5] or 0),
                        "id": r[6], "reference_txn_id": r[7],
                    }
                    for r in cur.fetchall()
                ]
        finally:
            release_db_connection(conn)
    except Exception:
        return []


def latest_charge_txn_id(account_type, email, feature=None):
    """Return the id of the most recent charge transaction (for refund targeting)."""
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                if feature:
                    cur.execute(
                        """
                        SELECT id FROM credit_transactions
                         WHERE account_type=%s AND email=%s AND txn_type='charge' AND feature=%s
                         ORDER BY id DESC LIMIT 1
                        """,
                        (account_type, email, feature),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id FROM credit_transactions
                         WHERE account_type=%s AND email=%s AND txn_type='charge'
                         ORDER BY id DESC LIMIT 1
                        """,
                        (account_type, email),
                    )
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            release_db_connection(conn)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# ATS hash-pair dedup
# ---------------------------------------------------------------------------
import re

def _stable_hash(*parts):
    h = hashlib.sha256()
    for p in parts:
        normalized = re.sub(r'\s+', ' ', (p or "")).strip().lower()
        h.update(normalized.encode("utf-8"))
    return h.hexdigest()


def ats_charge_or_free(account_type, email, cv_hash, jd_hash, feature="ATS", idempotency_key=None):
    """
    Charge 1 credit for an ATS check, unless the exact CV+JD hash pair was
    already checked by this user (then it's free). Returns {'ok','charged','free'}.
    """
    email = email.strip().lower()
    if is_test_account(email):
        return {"ok": True, "charged": 0, "free": True}
    cv_hash = _stable_hash(cv_hash)
    jd_hash = _stable_hash(jd_hash)
    cost = _feature_cost(feature)

    plan = get_plan_name(account_type, email)
    if account_type == "individual" and plan == "Free" and FREE_LIMITED_FEATURES.get(feature):
        if free_usage_available(account_type, email, feature):
            increment_free_usage(account_type, email, feature)
            return {"ok": True, "charged": 0, "free": True}
        return {"ok": False, "reason": "free_limit", "charged": 0, "free": False}

    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            wallet_id = _renew_and_purge_locked(cur, account_type, email)
            cur.execute(
                """
                SELECT 1 FROM ats_checks
                 WHERE account_type=%s AND email=%s AND cv_hash=%s AND jd_hash=%s LIMIT 1
                """,
                (account_type, email, cv_hash, jd_hash),
            )
            if cur.fetchone():
                return {"ok": True, "charged": 0, "free": True}

            # Charge first, then record the pair so the charge only happens once.
            res = _charge_with_conn(conn, cur, wallet_id, account_type, email, cost, feature, idempotency_key)
            if not res["ok"]:
                return res
            cur.execute(
                """
                INSERT INTO ats_checks (account_type, email, cv_hash, jd_hash)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (account_type, email, cv_hash, jd_hash),
            )
    finally:
        release_db_connection(conn)
    return {"ok": True, "charged": cost, "free": False}


def _charge_with_conn(conn, cur, wallet_id, account_type, email, amount, feature, idempotency_key):
    # Same logic as charge() but reuses an open transaction/cursor.
    if idempotency_key:
        cur.execute(
            """
            SELECT 1 FROM credit_transactions
             WHERE account_type=%s AND email=%s AND idempotency_key=%s LIMIT 1
            """,
            (account_type, email, idempotency_key),
        )
        if cur.fetchone():
            return {"ok": True, "charged": amount, "already_processed": True}
    cur.execute(
        "SELECT subscription_credits, pack_credits FROM credit_wallets WHERE id=%s FOR UPDATE",
        (wallet_id,),
    )
    sub, pack = cur.fetchone()
    sub, pack = int(sub or 0), int(pack or 0)
    remaining = amount
    take_sub = min(sub, remaining)
    remaining -= take_sub
    pack_charges = []
    if remaining > 0 and pack > 0:
        cur.execute(
            """
            SELECT id, credits_remaining FROM credit_packs
             WHERE account_type=%s AND email=%s AND credits_remaining > 0
                   AND expires_at > CURRENT_TIMESTAMP
             ORDER BY expires_at ASC FOR UPDATE
            """,
            (account_type, email),
        )
        for pid, prem in cur.fetchall():
            if remaining <= 0:
                break
            d = min(int(prem), remaining)
            remaining -= d
            pack_charges.append((pid, d))
            cur.execute(
                "UPDATE credit_packs SET credits_remaining = credits_remaining - %s WHERE id=%s",
                (d, pid),
            )
    if remaining > 0:
        return {"ok": False, "reason": "insufficient", "charged": 0}
    new_sub = sub - take_sub
    new_pack = pack - sum(d for _, d in pack_charges)
    cur.execute(
        "UPDATE credit_wallets SET subscription_credits=%s, pack_credits=%s, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (new_sub, new_pack, wallet_id),
    )
    balance_after = new_sub + new_pack
    group_id = str(uuid.uuid4())
    first = True
    if take_sub > 0:
        cur.execute(
            """
            INSERT INTO credit_transactions
                (account_type, email, feature, amount, txn_type, source,
                 balance_after, request_id, idempotency_key, group_id)
            VALUES (%s, %s, %s, %s, 'charge', 'subscription', %s, %s, %s, %s)
            """,
            (account_type, email, feature, -take_sub, balance_after,
             str(uuid.uuid4()), idempotency_key if first else None, group_id),
        )
        first = False
    for pid, d in pack_charges:
        cur.execute(
            """
            INSERT INTO credit_transactions
                (account_type, email, feature, amount, txn_type, source,
                 pack_id, balance_after, request_id, idempotency_key, group_id)
            VALUES (%s, %s, %s, %s, 'charge', 'pack', %s, %s, %s, %s, %s)
            """,
            (account_type, email, feature, -d, pid, balance_after,
             str(uuid.uuid4()), idempotency_key if first else None, group_id),
        )
        first = False
    if feature:
        try:
            record_credit_usage(email, feature, amount)
        except Exception:
            pass
    return {"ok": True, "charged": amount, "subscription_charged": take_sub,
            "pack_charged": sum(d for _, d in pack_charges)}


# ---------------------------------------------------------------------------
# F2F (live voice) mock interview — incremental block billing
# ---------------------------------------------------------------------------
def can_use_f2f(account_type, email):
    """Gate: Interview Pro plan, OR active pack credits, OR free one-time voice interview."""
    if is_test_account(email):
        return {"allowed": True, "free_once": False, "reason": None}
    email = email.strip().lower()
    bal = wallet_balance(account_type, email)
    if bal.get("is_free"):
        if bal.get("pack_credits", 0) > 0:
            # Free users with a purchased pack run a paid F2F session.
            return {"allowed": True, "free_once": False, "reason": None}
        if free_usage_available(account_type, email, "Voice Interview"):
            return {"allowed": True, "free_once": True, "reason": None}
        return {"allowed": False, "free_once": False, "reason": "free_used"}
    cfg = plan_config(bal.get("plan", ""))
    if cfg and cfg.get("f2f"):
        return {"allowed": True, "free_once": False, "reason": None}
    if bal.get("pack_credits", 0) > 0:
        return {"allowed": True, "free_once": False, "reason": None}
    return {"allowed": False, "free_once": False, "reason": "requires_interview_pro"}


def start_f2f_session(account_type, email, max_minutes=None, idempotency_key=None):
    """Open an F2F session. For Free-plan users this consumes the one-time voice interview."""
    email = email.strip().lower()
    gate = can_use_f2f(account_type, email)
    if not gate["allowed"]:
        return {"ok": False, "reason": gate["reason"]}

    if is_test_account(email):
        is_free = True
        session_max = 0
    elif gate["free_once"]:
        increment_free_usage(account_type, email, "Voice Interview")
        is_free = True
        session_max = FREE_VOICE_INTERVIEW_MINUTES
    else:
        is_free = False
        cfg = plan_config(wallet_balance(account_type, email).get("plan", ""))
        session_max = max_minutes or (cfg.get("f2f_max_minutes") if cfg else 0)

    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO f2f_sessions (account_type, email, status, is_free, max_minutes)
                VALUES (%s, %s, 'active', %s, %s) RETURNING id
                """,
                (account_type, email, is_free, session_max),
            )
            session_id = cur.fetchone()[0]
    finally:
        release_db_connection(conn)
    return {"ok": True, "session_id": session_id, "is_free": is_free, "max_minutes": session_max}


def _get_session(cur, session_id):
    cur.execute(
        "SELECT id, account_type, email, blocks_charged, is_free, max_minutes, status, last_charge_txn_id FROM f2f_sessions WHERE id=%s FOR UPDATE",
        (session_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "account_type": row[1], "email": row[2],
        "blocks_charged": int(row[3]), "is_free": bool(row[4]),
        "max_minutes": int(row[5] or 0), "status": row[6],
        "last_charge_txn_id": row[7],
    }


def charge_f2f_block(session_id, block_minutes=None, idempotency_key=None):
    """
    Charge the next F2F block. Returns {'ok', 'charged', 'blocks_charged', 'minutes'}.
    Stops (with ok=True, charged=0, reason='max_minutes') when the session cap is hit,
    and returns ok=False/'insufficient' when there is no credit to pay for the block.
    """
    block_minutes = block_minutes or F2F_BLOCK_MINUTES
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            sess = _get_session(cur, session_id)
            if not sess:
                return {"ok": False, "reason": "no_session"}
            if sess["status"] != "active":
                return {"ok": False, "reason": "not_active"}
            if sess["is_free"] or is_test_account(sess["email"]):
                is_test = is_test_account(sess["email"])
                end_flag = "" if is_test else ", status='ended'"
                cur.execute(
                    f"UPDATE f2f_sessions SET blocks_charged=blocks_charged+1, blocks_charged_minutes=blocks_charged_minutes+%s{end_flag} WHERE id=%s",
                    (block_minutes, session_id),
                )
                return {"ok": True, "charged": 0, "blocks_charged": sess["blocks_charged"] + 1,
                        "minutes": block_minutes, "is_free": True}

            if sess["max_minutes"] and sess["blocks_charged"] * F2F_BLOCK_MINUTES >= sess["max_minutes"]:
                cur.execute("UPDATE f2f_sessions SET status='ended' WHERE id=%s", (session_id,))
                return {"ok": True, "charged": 0, "reason": "max_minutes",
                        "blocks_charged": sess["blocks_charged"], "minutes": 0}

            cost = F2F_BLOCK_CREDITS
            wallet_id = _renew_and_purge_locked(cur, sess["account_type"], sess["email"])
            res = _charge_with_conn(conn, cur, wallet_id, sess["account_type"], sess["email"],
                                    cost, "F2F Interview", idempotency_key)
            if not res["ok"]:
                return res
            # find the txn id of the latest charge for refund targeting
            cur.execute(
                """
                SELECT id FROM credit_transactions
                 WHERE account_type=%s AND email=%s AND txn_type='charge'
                   AND feature='F2F Interview'
                 ORDER BY id DESC LIMIT 1
                """,
                (sess["account_type"], sess["email"]),
            )
            txn_row = cur.fetchone()
            cur.execute(
                """
                UPDATE f2f_sessions
                   SET blocks_charged=blocks_charged+1,
                       blocks_charged_minutes=blocks_charged_minutes+%s,
                       last_charge_txn_id=%s
                 WHERE id=%s
                """,
                (block_minutes, (txn_row[0] if txn_row else None), session_id),
            )
            return {"ok": True, "charged": cost,
                    "blocks_charged": sess["blocks_charged"] + 1,
                    "minutes": block_minutes, "is_free": False,
                    "charge_txn_id": txn_row[0] if txn_row else None}
    finally:
        release_db_connection(conn)


def refund_f2f_block(session_id, feature="F2F Interview", idempotency_key=None):
    """Refund the last charged F2F block (e.g. on a system error)."""
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            sess = _get_session(cur, session_id)
            if not sess or sess["is_free"]:
                return {"ok": True, "refunded": 0}
            txn_id = sess.get("last_charge_txn_id")
            if not txn_id:
                return {"ok": False, "reason": "no_charge_to_refund"}
            res = refund(sess["account_type"], sess["email"], F2F_BLOCK_CREDITS,
                         feature=feature, reference_txn_id=txn_id,
                         idempotency_key=idempotency_key)
            cur.execute(
                """
                UPDATE f2f_sessions
                   SET blocks_charged=GREATEST(0, blocks_charged-1),
                       blocks_charged_minutes=GREATEST(0, blocks_charged_minutes-%s),
                       last_charge_txn_id=NULL
                 WHERE id=%s
                """,
                (F2F_BLOCK_MINUTES, session_id),
            )
            return res
    finally:
        release_db_connection(conn)


def end_f2f_session(session_id):
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE f2f_sessions SET status='ended' WHERE id=%s AND status='active'",
                    (session_id,),
                )
        finally:
            release_db_connection(conn)
    except Exception:
        pass


def f2f_blocks_used(account_type, email, since=None):
    """Total F2F blocks charged for this user (this cycle by default)."""
    try:
        conn = get_db_connection()
        try:
            with conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    SELECT COALESCE(SUM(blocks_charged), 0) FROM f2f_sessions
                     WHERE account_type=%s AND email=%s AND is_free=FALSE
                    """,
                    (account_type, email),
                )
                return int(cur.fetchone()[0])
        finally:
            release_db_connection(conn)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# One-time migration of legacy credit columns into wallets
# ---------------------------------------------------------------------------
def backfill_wallets():
    """Copy legacy users.credits / business_users.credits into wallets (idempotent)."""
    conn = get_db_connection()
    try:
        with conn:
            cur = conn.cursor()
            # Individual users
            cur.execute("SELECT email, COALESCE(credits, 0), credit_cycle_start FROM users")
            for email, credits, cycle_start in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO credit_wallets
                        (account_type, email, plan, subscription_credits, cycle_start, next_renewal)
                    VALUES ('individual', %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP),
                            COALESCE(%s, CURRENT_TIMESTAMP) + %s * INTERVAL '1 day')
                    ON CONFLICT (account_type, email) DO NOTHING
                    """,
                    (email, _legacy_plan(cur, email), credits, cycle_start, cycle_start, CYCLE_DAYS),
                )
            # Business users
            cur.execute("SELECT email, COALESCE(credits, 0), current_plan, plan_expiry FROM business_users")
            for email, credits, plan_name, expiry in cur.fetchall():
                cur.execute(
                    """
                    INSERT INTO credit_wallets
                        (account_type, email, plan, subscription_credits, cycle_start, next_renewal)
                    VALUES ('business', %s, %s, %s, COALESCE(%s, CURRENT_TIMESTAMP),
                            COALESCE(%s, CURRENT_TIMESTAMP) + %s * INTERVAL '1 day')
                    ON CONFLICT (account_type, email) DO NOTHING
                    """,
                    (email, plan_name or "Free", credits, expiry, expiry, CYCLE_DAYS),
                )
    finally:
        release_db_connection(conn)


def _legacy_plan(cur, email):
    cur.execute(
        """
        SELECT plan FROM subscriptions WHERE user_email=%s AND status='active'
         ORDER BY end_date DESC LIMIT 1
        """,
        (email,),
    )
    row = cur.fetchone()
    return row[0] if row else "Free"
