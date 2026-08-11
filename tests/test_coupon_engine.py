"""Tests for coupon engine: generate / redeem / revoke / admin gate.

Mirrors `tests/test_session_auth_voucher.py` style — uses mocks for the
DB layer so we don't need a live Postgres connection.
"""
import os
import re
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import coupon_engine


class TestCodeFormat:
    def test_generate_code_format(self):
        for _ in range(20):
            code = coupon_engine._generate_code()
            assert code.startswith("CP-")
            # CP-XXXX-XXXX
            parts = code.split("-")
            assert len(parts) == 3
            assert len(parts[1]) == 4 and len(parts[2]) == 4
            for p in parts[1:]:
                assert re.fullmatch(r"[A-Z0-9]{4}", p), p


class TestIsAdminReExport:
    def test_admin_gate_uses_env(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "admin@x.com"}):
            assert coupon_engine.is_admin("admin@x.com") is True
            assert coupon_engine.is_admin("someone@else.com") is False

    def test_admin_gate_case_insensitive(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "admin@x.com"}):
            assert coupon_engine.is_admin("ADMIN@X.COM") is True

    def test_admin_gate_none_or_empty(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "admin@x.com"}):
            assert coupon_engine.is_admin(None) is False
            assert coupon_engine.is_admin("") is False


class TestGenerateCouponValidation:
    def test_credits_below_min_rejected(self):
        with mock.patch.object(coupon_engine, "create_coupon"):
            try:
                coupon_engine.generate_coupon(credits=0)
            except ValueError as e:
                assert "credits" in str(e).lower()
            else:
                raise AssertionError("expected ValueError")

    def test_credits_above_max_rejected(self):
        with mock.patch.object(coupon_engine, "create_coupon"):
            try:
                coupon_engine.generate_coupon(credits=100_000)
            except ValueError as e:
                assert "credits" in str(e).lower()
            else:
                raise AssertionError("expected ValueError")

    def test_max_redemptions_below_one_rejected(self):
        with mock.patch.object(coupon_engine, "create_coupon"):
            try:
                coupon_engine.generate_coupon(credits=10, max_redemptions=0)
            except ValueError as e:
                assert "max_redemptions" in str(e).lower()
            else:
                raise AssertionError("expected ValueError")

    def test_target_email_normalised_lowercase(self):
        with mock.patch.object(coupon_engine, "create_coupon") as cc:
            cc.return_value = None
            out = coupon_engine.generate_coupon(
                credits=10,
                target_email="  Mixed@Case.COM  ",
            )
            assert out["target_email"] == "mixed@case.com"

    def test_empty_target_email_becomes_none(self):
        with mock.patch.object(coupon_engine, "create_coupon"):
            out = coupon_engine.generate_coupon(credits=10, target_email="   ")
            assert out["target_email"] is None


class TestRedeemCoupon:
    def _fake_coupon(self, **overrides):
        base = {
            "code": "CP-TEST-CODE",
            "credits": 50,
            "max_redemptions": 1,
            "expires_at": None,
            "target_email": None,
            "status": "active",
        }
        base.update(overrides)
        return base

    def test_missing_inputs(self):
        assert coupon_engine.redeem_coupon("", "u@x.com")["ok"] is False
        assert coupon_engine.redeem_coupon("CP-AAAA-BBBB", "")["ok"] is False
        assert (
            coupon_engine.redeem_coupon("", "")["reason"]
            == "missing_code_or_email"
        )

    def test_not_found(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "not_found"},
        ):
            res = coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")
            assert res["ok"] is False
            assert res["reason"] == "not_found"

    def test_inactive(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "inactive"},
        ):
            assert (
                coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")["reason"]
                == "inactive"
            )

    def test_expired(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "expired"},
        ):
            assert (
                coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")["reason"]
                == "expired"
            )

    def test_max_redemptions(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "max_redemptions"},
        ):
            assert (
                coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")["reason"]
                == "max_redemptions"
            )

    def test_wrong_recipient(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "wrong_recipient"},
        ):
            assert (
                coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")["reason"]
                == "wrong_recipient"
            )

    def test_already_redeemed(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value={"error": "already_redeemed"},
        ):
            assert (
                coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")["reason"]
                == "already_redeemed"
            )

    def test_grant_failed(self):
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic",
            return_value=self._fake_coupon(credits=25),
        ), mock.patch.object(
            coupon_engine, "grant_credits",
            return_value={"ok": False, "reason": "db_error"},
        ):
            res = coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")
            assert res["ok"] is False
            assert res["reason"] == "grant_failed"

    def test_success_grants_credits(self):
        fake = self._fake_coupon(credits=75)
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic", return_value=fake,
        ), mock.patch.object(
            coupon_engine, "grant_credits",
            return_value={"ok": True, "granted": 75, "balance_after": 175},
        ) as gc:
            res = coupon_engine.redeem_coupon("cp-aaaa-bbbb", "User@X.com")
            assert res["ok"] is True
            assert res["code"] == "CP-AAAA-BBBB"
            assert res["credits"] == 75
            assert res["balance_after"] == 175
            assert res["duplicate"] is False
            # Verify the call site used the right idempotency key + reference
            args, kwargs = gc.call_args
            # grant_credits(account_type, email, amount, *, feature, ...)
            assert args[0] == "individual"
            assert args[1] == "user@x.com"
            assert args[2] == 75
            assert kwargs["feature"] == "Coupon: CP-AAAA-BBBB"
            assert kwargs["reference_code"] == "CP-AAAA-BBBB"
            assert kwargs["source"] == "gift"
            assert kwargs["idempotency_key"] == "coupon:CP-AAAA-BBBB:user@x.com"

    def test_success_duplicate_flag(self):
        fake = self._fake_coupon(credits=10)
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic", return_value=fake,
        ), mock.patch.object(
            coupon_engine, "grant_credits",
            return_value={"ok": True, "duplicate": True, "granted": 0,
                          "balance_after": 10},
        ):
            res = coupon_engine.redeem_coupon("CP-AAAA-BBBB", "u@x.com")
            assert res["ok"] is True
            assert res["duplicate"] is True
            assert res["credits"] == 10
            assert res["balance_after"] == 10

    def test_account_type_passed_through(self):
        fake = self._fake_coupon(credits=10)
        with mock.patch.object(
            coupon_engine, "redeem_coupon_atomic", return_value=fake,
        ), mock.patch.object(
            coupon_engine, "grant_credits",
            return_value={"ok": True, "granted": 10, "balance_after": 10},
        ) as gc:
            coupon_engine.redeem_coupon(
                "CP-AAAA-BBBB", "biz@x.com", account_type="business",
            )
            args, kwargs = gc.call_args
            assert args[0] == "business"
            assert args[1] == "biz@x.com"


class TestConstants:
    def test_min_credits(self):
        assert coupon_engine.MIN_CREDITS == 1

    def test_max_credits(self):
        assert coupon_engine.MAX_CREDITS == 10000

    def test_prefix(self):
        assert coupon_engine.COUPON_CODE_PREFIX == "CP"
