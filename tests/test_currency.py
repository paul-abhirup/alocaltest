"""Tests for currency resolution + formatting."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import currency


class TestCountryMap:
    def test_20_plus_countries(self):
        # We promised ~20 major currencies; the map must cover at least 20.
        assert len(currency.COUNTRY_TO_CURRENCY) >= 20

    def test_every_currency_in_map_is_in_supported(self):
        for cur in currency.COUNTRY_TO_CURRENCY.values():
            assert cur in currency.SUPPORTED_CURRENCIES, cur

    def test_supported_currencies_sorted_and_unique(self):
        s = list(currency.SUPPORTED_CURRENCIES)
        assert s == sorted(set(s))

    def test_known_mappings(self):
        assert currency.COUNTRY_TO_CURRENCY["IN"] == "INR"
        assert currency.COUNTRY_TO_CURRENCY["GB"] == "GBP"
        assert currency.COUNTRY_TO_CURRENCY["US"] == "USD"
        assert currency.COUNTRY_TO_CURRENCY["JP"] == "JPY"
        assert currency.COUNTRY_TO_CURRENCY["AE"] == "AED"
        assert currency.COUNTRY_TO_CURRENCY["BH"] == "BHD"
        assert currency.COUNTRY_TO_CURRENCY["AU"] == "AUD"


class TestPhonePrefix:
    def test_india(self):
        assert currency._country_from_phone("+91 98765 43210") == "IN"

    def test_uk(self):
        assert currency._country_from_phone("+44 7700 900123") == "GB"

    def test_uae(self):
        assert currency._country_from_phone("+971 50 123 4567") == "AE"

    def test_bahrain(self):
        assert currency._country_from_phone("+973 3300 1234") == "BH"

    def test_empty(self):
        assert currency._country_from_phone("") is None
        assert currency._country_from_phone(None) is None

    def test_no_plus(self):
        assert currency._country_from_phone("9876543210") is None

    def test_unknown_prefix(self):
        assert currency._country_from_phone("+999 1234") is None


class TestResolveCurrency:
    def test_explicit_wins(self):
        out = currency.resolve_currency(
            explicit="EUR",
            query_params={"cur": "USD"},
            request_headers={"cf-ipcountry": "GB"},
            user_phone="+91 9999999999",
        )
        assert out == "EUR"

    def test_query_param_overrides_header_and_phone(self):
        out = currency.resolve_currency(
            query_params={"cur": "JPY"},
            request_headers={"cf-ipcountry": "IN"},
            user_phone="+91 9999999999",
        )
        assert out == "JPY"

    def test_header_overrides_phone(self):
        out = currency.resolve_currency(
            request_headers={"cf-ipcountry": "DE"},
            user_phone="+91 9999999999",
        )
        assert out == "EUR"

    def test_phone_used_when_no_header(self):
        out = currency.resolve_currency(user_phone="+44 7700 900123")
        assert out == "GBP"

    def test_fallback_to_usd(self):
        assert currency.resolve_currency() == "USD"
        assert currency.resolve_currency(user_phone="") == "USD"

    def test_unknown_query_param_ignored(self):
        # Bogus ?cur=XYZ -> ignored -> falls back
        out = currency.resolve_currency(query_params={"cur": "XYZ"})
        assert out == "USD"

    def test_unknown_country_header_falls_back(self):
        out = currency.resolve_currency(
            request_headers={"cf-ipcountry": "ZZ"},
        )
        assert out == "USD"

    def test_header_case_insensitive(self):
        out = currency.resolve_currency(
            request_headers={"CF-IPCountry": "AU"},
        )
        assert out == "AUD"

    def test_x_country_code_fallback_header(self):
        out = currency.resolve_currency(
            request_headers={"X-Country-Code": "FR"},
        )
        assert out == "EUR"

    def test_list_query_param(self):
        # Streamlit query params can be lists
        out = currency.resolve_currency(query_params={"cur": ["INR"]})
        assert out == "INR"


class TestResolveCountry:
    def test_header(self):
        assert currency.resolve_country(request_headers={"cf-ipcountry": "IN"}) == "IN"

    def test_phone(self):
        assert currency.resolve_country(user_phone="+91 9999999999") == "IN"

    def test_none(self):
        assert currency.resolve_country() is None

    def test_invalid_header_skipped(self):
        assert currency.resolve_country(request_headers={"cf-ipcountry": "ZZZZ"}) is None


class TestFormatting:
    def test_usd(self):
        assert currency.format_amount(19.99, "USD") == "$19.99"

    def test_inr_whole_rupees(self):
        # INR has no minor-units override -> defaults to 2; uses comma sep
        assert currency.format_amount(1679.4, "INR") == "₹1,679.40"

    def test_jpy_no_decimals(self):
        assert currency.format_amount(3078, "JPY") == "¥3,078"

    def test_bhd_three_decimals(self):
        assert currency.format_amount(7.599, "BHD") == "BD7.599"

    def test_gbp(self):
        assert currency.format_amount(15.6, "GBP") == "£15.60"

    def test_unknown_currency_falls_back_to_iso_code(self):
        # "XYZ" not in SYMBOLS -> uses the ISO code as the symbol
        assert currency.format_amount(10, "XYZ") == "XYZ10.00"


class TestMinorUnits:
    def test_defaults(self):
        assert currency.minor_units_for("USD") == 2
        assert currency.minor_units_for("EUR") == 2
        assert currency.minor_units_for("GBP") == 2

    def test_zero_decimal(self):
        assert currency.minor_units_for("JPY") == 0
        assert currency.minor_units_for("KRW") == 0

    def test_three_decimal(self):
        assert currency.minor_units_for("BHD") == 3
        assert currency.minor_units_for("KWD") == 3


class TestRoundForCurrency:
    def test_jpy_rounds_to_int(self):
        assert currency.round_for_currency(19.99, "JPY") == 20.0
        assert isinstance(currency.round_for_currency(19.99, "JPY"), float)

    def test_bhd_three_decimals(self):
        assert currency.round_for_currency(1.23456, "BHD") == 1.235
