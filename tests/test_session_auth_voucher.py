import os
import unittest
from unittest import mock

import session_auth
import voucher_engine


class TestSessionAuth(unittest.TestCase):
    def test_hash_token_stable(self):
        h1 = session_auth._hash_token("abc123")
        h2 = session_auth._hash_token("abc123")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)  # sha256 hex

    def test_issue_session_returns_raw_token(self):
        with mock.patch.object(session_auth, "create_auth_session") as mock_create:
            raw = session_auth.issue_session("user@example.com", "individual")
            self.assertIsInstance(raw, str)
            self.assertTrue(len(raw) > 20)
            mock_create.assert_called_once()
            stored_hash = mock_create.call_args[0][0]
            self.assertEqual(stored_hash, session_auth._hash_token(raw))

    def test_issue_session_db_failure_returns_none(self):
        with mock.patch.object(session_auth, "create_auth_session", side_effect=RuntimeError("db down")):
            self.assertIsNone(session_auth.issue_session("user@example.com"))

    def test_validate_session_empty_token(self):
        self.assertIsNone(session_auth.validate_session(""))
        self.assertIsNone(session_auth.validate_session(None))

    def test_validate_session_invalid_token(self):
        with mock.patch.object(session_auth, "get_auth_session", return_value=None):
            self.assertIsNone(session_auth.validate_session("bogus"))

    def test_validate_session_individual_user(self):
        fake_user = {"email": "u@example.com", "name": "U"}
        with mock.patch.object(session_auth, "get_auth_session", return_value={"user_email": "u@example.com", "account_type": "individual"}), \
             mock.patch.object(session_auth, "get_user_data", return_value=fake_user):
            out = session_auth.validate_session("tok")
            self.assertEqual(out["user"], fake_user)
            self.assertEqual(out["account_type"], "individual")

    def test_validate_session_business_user(self):
        fake_user = {"email": "b@example.com", "company_name": "Acme"}
        with mock.patch.object(session_auth, "get_auth_session", return_value={"user_email": "b@example.com", "account_type": "business"}), \
             mock.patch.object(session_auth, "get_business_user", return_value=fake_user):
            out = session_auth.validate_session("tok")
            self.assertEqual(out["user"], fake_user)
            self.assertEqual(out["account_type"], "business")

    def test_validate_session_user_missing(self):
        with mock.patch.object(session_auth, "get_auth_session", return_value={"user_email": "gone@example.com", "account_type": "individual"}), \
             mock.patch.object(session_auth, "get_user_data", return_value=None):
            self.assertIsNone(session_auth.validate_session("tok"))

    def test_revoke_session(self):
        with mock.patch.object(session_auth, "delete_auth_session") as mock_del:
            session_auth.revoke_session("tok")
            mock_del.assert_called_once_with(session_auth._hash_token("tok"))

    def test_revoke_session_empty(self):
        with mock.patch.object(session_auth, "delete_auth_session") as mock_del:
            session_auth.revoke_session("")
            mock_del.assert_not_called()


class TestVoucherEngine(unittest.TestCase):
    def test_code_format(self):
        for _ in range(20):
            code = voucher_engine._generate_code()
            self.assertTrue(code.startswith("CV-"))
            parts = code.split("-")
            self.assertEqual(len(parts), 3)
            self.assertEqual(len(parts[1]), 4)
            self.assertEqual(len(parts[2]), 4)

    def test_is_admin_env_driven(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": "admin@x.com, owner@x.com"}):
            self.assertTrue(voucher_engine.is_admin("admin@x.com"))
            self.assertTrue(voucher_engine.is_admin("Owner@X.com"))  # case-insensitive
            self.assertFalse(voucher_engine.is_admin("someone@else.com"))

    def test_is_admin_empty_env(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAILS": ""}):
            self.assertFalse(voucher_engine.is_admin("admin@x.com"))

    def test_is_admin_none(self):
        self.assertFalse(voucher_engine.is_admin(None))
        self.assertFalse(voucher_engine.is_admin(""))

    def test_redeem_missing_args(self):
        self.assertFalse(voucher_engine.redeem_voucher("", "u@x.com")["ok"])
        self.assertFalse(voucher_engine.redeem_voucher("CV-AAAA-BBBB", "")["ok"])

    def test_redeem_rejected_by_atomic(self):
        with mock.patch.object(voucher_engine, "redeem_voucher_atomic", return_value={"error": "max_redemptions"}):
            res = voucher_engine.redeem_voucher("CV-AAAA-BBBB", "u@x.com")
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason"], "max_redemptions")

    def test_redeem_success_activates_plan(self):
        fake_v = {"plan": "Voucher Pro", "duration_days": 30}
        with mock.patch.object(voucher_engine, "redeem_voucher_atomic", return_value=fake_v), \
             mock.patch.object(voucher_engine, "purchase_plan", return_value={"ok": True, "plan": "Voucher Pro", "credits": 120}) as mock_pp:
            res = voucher_engine.redeem_voucher("cv-aaaa-bbbb", "User@X.com")
            self.assertTrue(res["ok"])
            self.assertEqual(res["plan"], "Voucher Pro")
            self.assertEqual(res["credits"], 120)
            self.assertEqual(res["duration_days"], 30)
            # Email lowercased; duration_days passed through
            mock_pp.assert_called_once_with("individual", "user@x.com", "Voucher Pro",
                                            stripe_session_id=None, duration_days=30)

    def test_redeem_plan_activation_failure(self):
        fake_v = {"plan": "Voucher Pro", "duration_days": 30}
        with mock.patch.object(voucher_engine, "redeem_voucher_atomic", return_value=fake_v), \
             mock.patch.object(voucher_engine, "purchase_plan", return_value={"ok": False}):
            res = voucher_engine.redeem_voucher("CV-AAAA-BBBB", "u@x.com")
            self.assertFalse(res["ok"])
            self.assertEqual(res["reason"], "plan_activation_failed")


class TestVoucherPlanGating(unittest.TestCase):
    """Voucher Pro must NOT allow the F2F (ElevenLabs) live voice interview."""

    def test_voucher_pro_config(self):
        import pricing
        cfg = pricing.plan_config("Voucher Pro")
        self.assertIsNotNone(cfg)
        self.assertFalse(cfg["f2f"])
        self.assertEqual(cfg["f2f_max_minutes"], 0)
        self.assertTrue(cfg["voucher_only"])
        self.assertEqual(cfg["monthly_credits"], 120)


if __name__ == "__main__":
    unittest.main()
