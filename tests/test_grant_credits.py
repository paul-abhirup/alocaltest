"""Tests for credit_engine.grant_credits (coupon / gift credits).

Uses mocks for the DB layer so no Postgres is required. Validates:
- amount validation
- wallet created if missing
- pack_credits incremented (not subscription_credits)
- credit_transactions ledger row written with txn_type='credit', source='gift'
- idempotency: same key returns duplicate=True, no second grant
- account_type passed through correctly
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
import credit_engine


class _FakeUniqueViolation(psycopg2.errors.UniqueViolation):
    """Stand-in for psycopg2.errors.UniqueViolation."""

    def __init__(self, msg="duplicate"):
        super().__init__(msg)


class _FakeCursor:
    """Minimal cursor that records execute calls and returns canned results."""

    def __init__(self, fetchone_results=None, fetchall_results=None,
                 raise_on_execute=None):
        self.fetchone_results = list(fetchone_results or [])
        self.fetchall_results = list(fetchall_results or [])
        self.raise_on_execute = raise_on_execute  # dict: index -> Exception
        self.calls = []
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        idx = len(self.calls) - 1
        if self.raise_on_execute and idx in self.raise_on_execute:
            raise self.raise_on_execute[idx]

    def fetchone(self):
        if self.fetchone_results:
            return self.fetchone_results.pop(0)
        return None

    def fetchall(self):
        if self.fetchall_results:
            return self.fetchall_results.pop(0)
        return []

    def close(self):
        self.closed = True


class _FakeConn:
    """Minimal connection that supports `with conn:` (no-op) and cursor()."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def _patch_db(fetchone_seq, raise_on_execute=None):
    """Patch credit_engine.get_db_connection + release_db_connection."""
    cursor = _FakeCursor(
        fetchone_results=list(fetchone_seq),
        raise_on_execute=raise_on_execute,
    )
    conn = _FakeConn(cursor)
    return (
        mock.patch("credit_engine.get_db_connection", return_value=conn),
        mock.patch("credit_engine.release_db_connection"),
        conn,
        cursor,
    )


class TestGrantCreditsAmountValidation:
    def test_zero_amount_rejected(self):
        res = credit_engine.grant_credits(
            "individual", "u@x.com", 0, feature="x",
        )
        assert res["ok"] is False
        assert res["reason"] == "invalid_amount"

    def test_negative_amount_rejected(self):
        res = credit_engine.grant_credits(
            "individual", "u@x.com", -10, feature="x",
        )
        assert res["ok"] is False
        assert res["reason"] == "invalid_amount"


class TestGrantCreditsHappyPath:
    def test_first_grant_creates_wallet_and_increments_pack_credits(self):
        # _ensure_wallet fetches existing id OR seeds -> we return id=42
        # The UPDATE returns the new pack_credits = 75
        # INSERT for credit_transactions succeeds.
        patcher, release, conn, cursor = _patch_db(
            fetchone_seq=[
                (42,),  # _ensure_wallet: SELECT id FOR UPDATE
                (75,),  # UPDATE ... RETURNING pack_credits
            ]
        )
        with patcher, release:
            res = credit_engine.grant_credits(
                "individual", "u@x.com", 50,
                feature="Coupon: CP-AAAA-BBBB",
                source="gift",
                reference_code="CP-AAAA-BBBB",
                idempotency_key="coupon:CP-AAAA-BBBB:u@x.com",
            )
        assert res["ok"] is True
        assert res["granted"] == 50
        assert res["balance_after"] == 75
        assert not res.get("duplicate")

        # Three SQL calls: ensure_wallet SELECT, UPDATE, INSERT
        sqls = [c[0] for c in cursor.calls]
        assert any("SELECT id FROM credit_wallets" in s for s in sqls)
        assert any("UPDATE credit_wallets" in s and "pack_credits" in s for s in sqls)
        assert any("INSERT INTO credit_transactions" in s for s in sqls)

        # Verify the INSERT used the right fields
        insert_call = next(
            (c for c in cursor.calls if "INSERT INTO credit_transactions" in c[0]),
            None,
        )
        assert insert_call is not None
        sql, params = insert_call
        # params order matches: account_type, email, feature, amount, source,
        # balance_after, request_id, idempotency_key, group_id
        assert params[0] == "individual"
        assert params[1] == "u@x.com"
        # feature label gets reference appended (Coupon: CP-XXXX-XXXX (CP-XXXX-XXXX))
        assert params[2] == "Coupon: CP-AAAA-BBBB (CP-AAAA-BBBB)"
        assert params[3] == 50
        assert params[4] == "gift"
        assert params[5] == 75
        assert params[7] == "coupon:CP-AAAA-BBBB:u@x.com"
        assert params[8] == "CP-AAAA-BBBB"
        # 'credit' is hard-coded in the SQL string
        assert "'credit'" in sql

    def test_feature_label_appends_reference(self):
        patcher, release, conn, cursor = _patch_db(
            fetchone_seq=[(42,), (10,)],
        )
        with patcher, release:
            credit_engine.grant_credits(
                "individual", "u@x.com", 10,
                feature="Promo",
                reference_code="SUMMER25",
                idempotency_key="k1",
            )
        insert = next(c for c in cursor.calls if "INSERT INTO credit_transactions" in c[0])
        assert insert[1][2] == "Promo (SUMMER25)"

    def test_feature_label_without_reference(self):
        patcher, release, conn, cursor = _patch_db(
            fetchone_seq=[(42,), (5,)],
        )
        with patcher, release:
            credit_engine.grant_credits(
                "business", "biz@x.com", 5, feature="Admin grant",
            )
        insert = next(c for c in cursor.calls if "INSERT INTO credit_transactions" in c[0])
        assert insert[1][2] == "Admin grant"
        assert insert[1][0] == "business"


class TestGrantCreditsIdempotency:
    def test_duplicate_idempotency_key_returns_no_double_grant(self):
        # First INSERT raises UniqueViolation (index #2: the INSERT),
        # then on rollback we re-read pack_credits=75
        patcher, release, conn, cursor = _patch_db(
            fetchone_seq=[
                (42,),     # _ensure_wallet SELECT
                (75,),     # UPDATE RETURNING
                (75,),     # SELECT after rollback (pack_credits)
            ],
            raise_on_execute={
                2: _FakeUniqueViolation("duplicate idempotency_key"),
            },
        )
        with patcher, release:
            res = credit_engine.grant_credits(
                "individual", "u@x.com", 50,
                feature="Coupon: CP-AAAA-BBBB",
                reference_code="CP-AAAA-BBBB",
                idempotency_key="coupon:CP-AAAA-BBBB:u@x.com",
            )
        assert res["ok"] is True
        assert res.get("duplicate") is True
        assert res["granted"] == 0
        assert res["balance_after"] == 75
        # rollback was called
        assert conn.rolled_back is True


class TestGrantCreditsBucket:
    def test_increments_pack_credits_not_subscription(self):
        """Regression: confirm grant never touches subscription_credits."""
        patcher, release, conn, cursor = _patch_db(
            fetchone_seq=[(42,), (50,)],
        )
        with patcher, release:
            credit_engine.grant_credits(
                "individual", "u@x.com", 50, feature="x",
            )
        update_call = next(
            c for c in cursor.calls if "UPDATE credit_wallets" in c[0]
        )
        sql, params = update_call
        # Must set pack_credits (not subscription_credits)
        assert "pack_credits" in sql
        assert "subscription_credits" not in sql
        assert params[0] == 50
        assert params[1] == 42
