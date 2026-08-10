"""Tests for pricing.price_for and related helpers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fx
import pricing
from pricing import price_for, price_for_jobsqa, INR_OVERRIDE


# --- Deterministic FX for tests -------------------------------------------------
# We monkey-patch the FX layer with static rates so tests don't hit the network
# and don't break when live rates change.

TEST_RATES = {
    "USD": 1.0,
    "EUR": 0.92,
    "GBP": 0.78,
    "JPY": 154.0,
    "INR": 84.0,
    "BHD": 0.38,
    "AUD": 1.50,
    "AED": 3.67,
    "CAD": 1.36,
    "SGD": 1.34,
    "ZAR": 18.20,
    "MYR": 4.65,
    "PHP": 56.50,
}


def _convert(amount, target, base="USD"):
    return amount * TEST_RATES[target.upper()] / TEST_RATES[base.upper()]


def _get_rate(target, base="USD"):
    return TEST_RATES[target.upper()] / TEST_RATES[base.upper()]


def setup_module(_module):
    fx._CACHE.clear()
    fx.convert = _convert
    fx.get_rate = _get_rate


class TestPriceFor:
    def test_usd_passthrough(self):
        amount, cur = price_for({"price_usd": 19.99, "name": "Career Pro"}, "USD")
        assert cur == "USD"
        assert amount == 19.99

    def test_india_override_career_pro(self):
        amount, cur = price_for(
            {"price_usd": 19.99, "name": "Career Pro"},
            "USD",
            country="IN",
        )
        assert cur == "INR"
        assert amount == INR_OVERRIDE["Career Pro"]

    def test_india_override_ignores_other_currency(self):
        # If the user is in India, INR override wins even if currency=USD passed.
        amount, cur = price_for(
            {"price_usd": 29.99, "name": "Interview Pro"},
            "USD",
            country="IN",
        )
        assert cur == "INR"
        assert amount == INR_OVERRIDE["Interview Pro"]

    def test_india_override_quick_pack(self):
        amount, cur = price_for(
            pricing.PACKS[0],  # Quick Pack
            "EUR",
            country="IN",
        )
        assert cur == "INR"
        assert amount == INR_OVERRIDE["Quick Pack"]

    def test_country_lowercase_normalised(self):
        amount, cur = price_for(
            {"price_usd": 19.99, "name": "Career Pro"},
            "USD",
            country="in",
        )
        assert cur == "INR"

    def test_non_india_uses_fx(self):
        amount, cur = price_for(
            {"price_usd": 10.0, "name": "Some Plan"},
            "GBP",
        )
        assert cur == "GBP"
        # 10 * 0.78 = 7.8
        assert amount == 7.8

    def test_jpy_no_decimals(self):
        amount, cur = price_for(
            {"price_usd": 19.99, "name": "Some Plan"},
            "JPY",
        )
        assert cur == "JPY"
        # 19.99 * 154 = 3078.46 -> 3078 (rounded to int)
        assert amount == 3078

    def test_bhd_three_decimals(self):
        amount, cur = price_for(
            {"price_usd": 19.99, "name": "Some Plan"},
            "BHD",
        )
        assert cur == "BHD"
        # 19.99 * 0.38 = 7.5962 -> rounded to 3dp = 7.596
        assert amount == 7.596

    def test_zero_price(self):
        amount, cur = price_for({"price_usd": 0, "name": "Free Trial"}, "EUR")
        assert amount == 0
        assert cur == "EUR"

    def test_unknown_name_no_override(self):
        # Plan name not in INR_OVERRIDE -> falls through to FX
        amount, cur = price_for(
            {"price_usd": 50.0, "name": "Future Pro"},
            "USD",
            country="IN",
        )
        assert cur == "USD"
        assert amount == 50.0

    def test_all_plans_have_a_currency(self):
        # Smoke-test every entry in pricing.PLANS and pricing.PACKS
        for name, cfg in pricing.PLANS.items():
            if cfg.get("voucher_only"):
                continue
            for ccy in ["USD", "EUR", "JPY", "BHD", "INR"]:
                amount, cur = price_for(
                    {"price_usd": cfg["price_usd"], "name": name},
                    ccy,
                    country="IN" if ccy != "USD" else None,
                )
                assert isinstance(amount, (int, float))
                assert cur in ("USD", "INR")
        for pack in pricing.PACKS:
            for ccy in ["USD", "EUR", "JPY"]:
                amount, cur = price_for(pack, ccy)
                assert isinstance(amount, (int, float))
                assert cur == ccy


class TestPriceForJobsqa:
    def test_india_override(self):
        amount, cur = price_for_jobsqa("USD", country="IN")
        assert cur == "INR"
        assert amount == 899

    def test_usd(self):
        amount, cur = price_for_jobsqa("USD")
        assert cur == "USD"
        assert amount == 1499  # $14.99 in cents

    def test_eur_uses_fx(self):
        amount, cur = price_for_jobsqa("EUR", country="DE")
        assert cur == "EUR"
        # 14.99 * 0.92 = 13.7908 -> 1379 cents
        assert amount == 1379

    def test_jpy(self):
        amount, cur = price_for_jobsqa("JPY", country="JP")
        assert cur == "JPY"
        # 14.99 * 154 = 2308.46 -> 2308 (minor units = 0)
        assert amount == 2308


class TestFxStaticRates:
    def test_static_rates_present_for_all_supported(self):
        import currency as _currency
        for cur in _currency.SUPPORTED_CURRENCIES:
            assert cur in fx.STATIC_RATES_USD, cur

    def test_static_rate_for_usd_is_one(self):
        assert fx.STATIC_RATES_USD["USD"] == 1.0

    def test_static_rates_positive(self):
        for cur, rate in fx.STATIC_RATES_USD.items():
            assert rate > 0, cur
