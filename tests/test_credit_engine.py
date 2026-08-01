"""
Credit-engine tests against a dedicated Postgres database (cvolvepro_test),
which must be reachable — normally the docker `cvolve-pg` container (port 5433).

Run:  .venv/bin/python -m pytest tests/test_credit_engine.py -q
"""
import os
import sys
import threading

# ── Point the app's DB layer at the isolated test database BEFORE imports ──
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "5433"))
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASSWORD", "cvolve_local_2026")
TEST_DB = "cvolvepro_test"

os.environ["DB_HOST"] = DB_HOST
os.environ["DB_PORT"] = str(DB_PORT)
os.environ["DB_USER"] = DB_USER
os.environ["DB_PASSWORD"] = DB_PASS
os.environ["DB_NAME"] = TEST_DB
os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)

_conn = psycopg2.connect(
    host=DB_HOST, port=DB_PORT, dbname="postgres", user=DB_USER, password=DB_PASS,
)
_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
with _conn.cursor() as cur:
    cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TEST_DB,))
    if not cur.fetchone():
        cur.execute(f'CREATE DATABASE {TEST_DB}')
_conn.close()

import pytest  # noqa: E402

from database import init_db, get_db_connection  # noqa: E402
import credit_engine as ce  # noqa: E402
import pricing  # noqa: E402


def _db_reachable():
    try:
        c = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=TEST_DB, user=DB_USER, password=DB_PASS)
        c.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="Docker Postgres (cvolvepro_test on :5433) not available",
)


def _truncate():
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                TRUNCATE credit_wallets, credit_transactions, credit_packs,
                         ats_checks, free_usage_counters, f2f_sessions,
                         credit_usage, payments, subscriptions, business_users,
                         users, user_sessions CASCADE
                """
            )
    conn.close()


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    _truncate()
    yield
    _truncate()


def _make_user(email, plan="Free", credits=0):
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (name, email, auth_provider, credits)
                VALUES ('Tester', %s, 'email', %s)
                """,
                (email, credits),
            )
            if plan:
                cur.execute(
                    """
                    INSERT INTO subscriptions (user_email, plan, status, start_date, end_date)
                    VALUES (%s, %s, 'active', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 days')
                    """,
                    (email, plan),
                )
    conn.close()


def _make_business(email, plan_name="Starter", credits=0):
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO business_users (company_name, owner_name, email, password_hash, plan_name, credits)
                VALUES ('Acme', 'Owner', %s, 'x', %s, %s)
                """,
                (email, plan_name, credits),
            )
    conn.close()


def _balance(email, account_type="individual"):
    return ce.wallet_balance(account_type, email)


# ---------------------------------------------------------------------------
# Free plan counters
# ---------------------------------------------------------------------------
def test_free_plan_counter_limits():
    _make_user("free@example.com", plan="Free", credits=0)
    ok1 = ce.spend_credits("individual", "free@example.com", "CV")
    assert ok1["ok"] and ok1["free_used"]
    ok2 = ce.spend_credits("individual", "free@example.com", "CV")
    assert not ok2["ok"] and ok2["reason"] == "free_limit"

    for _ in range(3):
        assert ce.spend_credits("individual", "free@example.com", "ATS")["ok"]
    assert not ce.spend_credits("individual", "free@example.com", "ATS")["ok"]


def test_free_plan_summary_shows_used():
    _make_user("free2@example.com", plan="Free", credits=0)
    ce.spend_credits("individual", "free2@example.com", "CV")
    bal = _balance("free2@example.com")
    assert bal["is_free"] is True
    assert bal["free_counters"]["CV"]["used"] == 1
    assert bal["free_counters"]["CV"]["limit"] == 1


def test_paid_users_not_counter_limited():
    _make_user("paid@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "paid@example.com", "Career Pro")
    assert ce.spend_credits("individual", "paid@example.com", "CV", amount=3)["ok"]
    assert ce.spend_credits("individual", "paid@example.com", "CV", amount=3)["ok"]


# ---------------------------------------------------------------------------
# Charging: subscription first, then packs
# ---------------------------------------------------------------------------
def test_charge_subscription_then_pack():
    _make_user("mix@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "mix@example.com", "Career Pro")  # 80
    ce.purchase_pack("individual", "mix@example.com", "Starter Pack")  # 30

    res = ce.charge("individual", "mix@example.com", 100, feature="CV")
    assert res["ok"]
    assert res["subscription_charged"] == 80
    assert res["pack_charged"] == 20
    bal = _balance("mix@example.com")
    assert bal["total"] == 10
    assert bal["subscription_credits"] == 0
    assert bal["pack_credits"] == 10


def test_charge_insufficient_returns_false_no_ledger():
    _make_user("poor@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "poor@example.com", "Career Pro")
    ce.charge("individual", "poor@example.com", 50, feature="ATS")
    res = ce.charge("individual", "poor@example.com", 100, feature="ATS")
    assert not res["ok"]
    bal = _balance("poor@example.com")
    assert bal["total"] == 30
    txns = ce.recent_transactions("individual", "poor@example.com", limit=50)
    failed_charges = [t for t in txns if t["txn_type"] == "charge" and t["amount"] == -100]
    assert failed_charges == []


def test_charge_zero_amount_is_noop():
    _make_user("zero@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "zero@example.com", "Career Pro")
    res = ce.charge("individual", "zero@example.com", 0, feature="ATS")
    assert res["ok"]
    assert _balance("zero@example.com")["total"] == 80


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------
def test_concurrent_charges_never_negative():
    _make_user("race@example.com", plan="Interview Pro", credits=0)
    ce.purchase_plan("individual", "race@example.com", "Interview Pro")  # 120
    results = []
    lock = threading.Lock()

    def worker():
        r = ce.charge("individual", "race@example.com", 30, feature="ATS")
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r["ok"]]
    assert len(successes) == 4  # 4×30 = 120 exactly
    bal = _balance("race@example.com")
    assert bal["total"] == 0
    assert bal["total"] >= 0
    assert all(r["ok"] or r["reason"] == "insufficient" for r in results)


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------
def test_refund_restores_and_ledger():
    _make_user("refund@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "refund@example.com", "Career Pro")
    res = ce.charge("individual", "refund@example.com", 10, feature="CV")
    assert res["ok"]
    txn_id = ce.latest_charge_txn_id("individual", "refund@example.com")

    before = _balance("refund@example.com")["total"]
    r = ce.refund("individual", "refund@example.com", 10, feature="CV",
                  reference_txn_id=txn_id)
    assert r["ok"]
    assert _balance("refund@example.com")["total"] == before + 10
    txns = ce.recent_transactions("individual", "refund@example.com", limit=5)
    assert any(t["txn_type"] == "refund" and t["reference_txn_id"] == txn_id for t in txns)


def test_refund_split_source_restores_both():
    _make_user("split@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "split@example.com", "Career Pro")  # 80 sub
    ce.purchase_pack("individual", "split@example.com", "Starter Pack")  # 30 pack
    ce.charge("individual", "split@example.com", 100, feature="CV")
    txn_id = ce.latest_charge_txn_id("individual", "split@example.com")
    bal = _balance("split@example.com")
    assert bal["total"] == 10
    r = ce.refund("individual", "split@example.com", 100, feature="CV", reference_txn_id=txn_id)
    assert r["ok"]
    bal = _balance("split@example.com")
    assert bal["total"] == 110
    assert bal["subscription_credits"] == 80
    assert bal["pack_credits"] == 30


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def test_idempotency_key_prevents_double_charge():
    _make_user("idem@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "idem@example.com", "Career Pro")
    r1 = ce.charge("individual", "idem@example.com", 10, feature="ATS", idempotency_key="req-1")
    r2 = ce.charge("individual", "idem@example.com", 10, feature="ATS", idempotency_key="req-1")
    assert r1["ok"] and r2["ok"]
    assert _balance("idem@example.com")["total"] == 70


# ---------------------------------------------------------------------------
# Monthly renewal
# ---------------------------------------------------------------------------
def test_monthly_renewal_resets_keeps_packs():
    _make_user("renew@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "renew@example.com", "Career Pro")  # 80
    ce.purchase_pack("individual", "renew@example.com", "Starter Pack")  # 30
    ce.charge("individual", "renew@example.com", 10, feature="CV")

    # force the cycle to have expired
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE credit_wallets
                   SET next_renewal = CURRENT_TIMESTAMP - INTERVAL '1 day'
                 WHERE account_type='individual' AND email='renew@example.com'
                """
            )
    conn.close()

    bal = _balance("renew@example.com")
    assert bal["subscription_credits"] == 80      # reset to allowance
    assert bal["pack_credits"] == 30              # pack preserved
    txns = ce.recent_transactions("individual", "renew@example.com", limit=50)
    assert any(t["txn_type"] == "renewal" for t in txns)


def test_renewal_expires_unused_monthly():
    _make_user("renew2@example.com", plan="Interview Pro", credits=0)
    ce.purchase_plan("individual", "renew2@example.com", "Interview Pro")  # 120
    ce.charge("individual", "renew2@example.com", 40, feature="CV")  # 80 left
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE credit_wallets SET next_renewal = CURRENT_TIMESTAMP - INTERVAL '1 day' WHERE account_type='individual' AND email='renew2@example.com'"
            )
    conn.close()
    bal = _balance("renew2@example.com")
    assert bal["subscription_credits"] == 120


# ---------------------------------------------------------------------------
# Packs
# ---------------------------------------------------------------------------
def test_pack_purchase_and_expiry():
    _make_user("pack@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "pack@example.com", "Career Pro")
    ce.purchase_pack("individual", "pack@example.com", "Pro Pack")  # 60
    bal = _balance("pack@example.com")
    assert bal["pack_credits"] == 60
    assert len(bal["packs"]) == 1
    assert bal["pack_expiry"] is not None

    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE credit_packs SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 day' WHERE account_type='individual' AND email='pack@example.com'"
            )
    conn.close()
    bal = _balance("pack@example.com")
    assert bal["pack_credits"] == 0
    assert bal["packs"] == []


def test_unknown_pack_rejected():
    _make_user("badpack@example.com", plan="Career Pro", credits=0)
    res = ce.purchase_pack("individual", "badpack@example.com", "Nope Pack")
    assert not res["ok"]


# ---------------------------------------------------------------------------
# ATS hash-pair dedup
# ---------------------------------------------------------------------------
def test_ats_dedup_free_recheck():
    _make_user("ats@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "ats@example.com", "Career Pro")
    r1 = ce.ats_charge_or_free("individual", "ats@example.com", "cv-1", "jd-1")
    assert r1["ok"] and r1["charged"] == 1
    r2 = ce.ats_charge_or_free("individual", "ats@example.com", "cv-1", "jd-1")
    assert r2["ok"] and r2["charged"] == 0 and r2["free"]
    r3 = ce.ats_charge_or_free("individual", "ats@example.com", "cv-1", "jd-2")
    assert r3["ok"] and r3["charged"] == 1
    assert _balance("ats@example.com")["total"] == 78


# ---------------------------------------------------------------------------
# F2F block billing
# ---------------------------------------------------------------------------
def test_f2f_blocks_charge_incrementally():
    _make_user("f2f@example.com", plan="Interview Pro", credits=0)
    ce.purchase_plan("individual", "f2f@example.com", "Interview Pro")  # 120
    s = ce.start_f2f_session("individual", "f2f@example.com")
    assert s["ok"] and not s["is_free"]

    b1 = ce.charge_f2f_block(s["session_id"])
    assert b1["ok"] and b1["charged"] == 10
    b2 = ce.charge_f2f_block(s["session_id"])
    assert b2["ok"] and b2["charged"] == 10
    assert _balance("f2f@example.com")["total"] == 100

    # cap of 60 min = 4 blocks on Interview Pro
    for _ in range(2):
        ce.charge_f2f_block(s["session_id"])
    b5 = ce.charge_f2f_block(s["session_id"])
    assert b5["ok"] and b5["charged"] == 0 and b5["reason"] == "max_minutes"
    assert _balance("f2f@example.com")["total"] == 80


def test_f2f_stops_when_credits_insufficient():
    _make_user("f2f2@example.com", plan="Free", credits=0)
    ce.purchase_pack("individual", "f2f2@example.com", "Starter Pack")  # 30
    s = ce.start_f2f_session("individual", "f2f2@example.com")
    assert s["ok"]
    for _ in range(3):
        r = ce.charge_f2f_block(s["session_id"])
        assert r["ok"] and r["charged"] == 10
    r4 = ce.charge_f2f_block(s["session_id"])
    assert not r4["ok"] and r4["reason"] == "insufficient"


def test_f2f_refund_block_on_error():
    _make_user("f2f3@example.com", plan="Interview Pro", credits=0)
    ce.purchase_plan("individual", "f2f3@example.com", "Interview Pro")
    s = ce.start_f2f_session("individual", "f2f3@example.com")
    ce.charge_f2f_block(s["session_id"])
    ce.charge_f2f_block(s["session_id"])
    assert _balance("f2f3@example.com")["total"] == 100
    rr = ce.refund_f2f_block(s["session_id"])
    assert rr["ok"]
    assert _balance("f2f3@example.com")["total"] == 110


def test_f2f_free_once_voice_interview():
    _make_user("voice@example.com", plan="Free", credits=0)
    s = ce.start_f2f_session("individual", "voice@example.com")
    assert s["ok"] and s["is_free"]
    b = ce.charge_f2f_block(s["session_id"])
    assert b["ok"] and b["charged"] == 0
    # second free session blocked
    s2 = ce.start_f2f_session("individual", "voice@example.com")
    assert not s2["ok"]


def test_f2f_gate_requires_interview_pro_or_pack():
    _make_user("career@example.com", plan="Career Pro", credits=0)
    ce.purchase_plan("individual", "career@example.com", "Career Pro")  # no F2F, no pack
    gate = ce.can_use_f2f("individual", "career@example.com")
    assert not gate["allowed"]
    ce.purchase_pack("individual", "career@example.com", "Starter Pack")
    assert ce.can_use_f2f("individual", "career@example.com")["allowed"]


def test_f2f_test_account_unlimited_free():
    s = ce.start_f2f_session("individual", "tester@cvolvepro.com")
    assert s["ok"] and s["is_free"]
    for _ in range(5):
        b = ce.charge_f2f_block(s["session_id"])
        assert b["ok"] and b["charged"] == 0
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM f2f_sessions WHERE id=%s", (s["session_id"],))
            status = cur.fetchone()[0]
    conn.close()
    assert status == "active"


# ---------------------------------------------------------------------------
# Business accounts
# ---------------------------------------------------------------------------
def test_business_charging():
    _make_business("biz@example.com", plan_name="Starter", credits=0)
    ce.purchase_plan("business", "biz@example.com", "Starter", stripe_session_id="sess-biz-1")
    res = ce.charge("business", "biz@example.com", 5, feature="ATS")
    assert res["ok"] and res["subscription_charged"] == 5
    bal = _balance("biz@example.com", account_type="business")
    assert bal["total"] == 495  # Starter = 500 credits


def test_corporate_plan_legacy_prefix_mapping():
    assert pricing.corporate_plan_config("Corporate Starter") == pricing.CORPORATE_PLANS["Starter"]
    assert pricing.corporate_plan_config("Corporate Enterprise") == pricing.CORPORATE_PLANS["Enterprise"]
    assert pricing.corporate_plan_config("Starter") == pricing.CORPORATE_PLANS["Starter"]

    _make_business("legacybiz@example.com", plan_name="Corporate Starter", credits=0)
    res = ce.purchase_plan("business", "legacybiz@example.com", "Corporate Starter",
                           stripe_session_id="sess-legacy-biz")
    assert res["ok"] and res["credits"] == 500
    bal = _balance("legacybiz@example.com", account_type="business")
    assert bal["total"] == 500


# ---------------------------------------------------------------------------
# Test account bypass
# ---------------------------------------------------------------------------
def test_test_account_bypass():
    r = ce.charge("individual", "tester@cvolvepro.com", 10, feature="ATS")
    assert r["ok"] and r["charged"] == 0
    assert ce.has_enough("individual", "tester@cvolvepro.com", feature="CV")
    assert _balance("tester@cvolvepro.com")["total"] >= 1_000_000


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
def test_backfill_wallets_from_legacy():
    _make_user("legacy@example.com", plan="Free", credits=42)
    # drop wallet if any, then backfill
    conn = get_db_connection()
    with conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM credit_wallets WHERE account_type='individual' AND email='legacy@example.com'")
    conn.close()
    ce.backfill_wallets()
    bal = _balance("legacy@example.com")
    assert bal["total"] == 42
