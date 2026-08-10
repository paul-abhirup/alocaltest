"""
Single source of truth for currency resolution and formatting.

Currencies are picked from (in priority order):
  1. Manual override via `?cur=` query param
  2. Country detected from request headers (CF-IPCountry / X-Country-Code)
  3. Country inferred from the user's phone dial code
  4. Fallback to "USD"

We support ~20 major ISO-4217 currencies. The full map lives in
`COUNTRY_TO_CURRENCY`. Countries not in the map fall back to USD via
`resolve_currency`.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping


# ---------------------------------------------------------------------------
# Static maps
# ---------------------------------------------------------------------------

# ISO-3166 alpha-2 -> ISO-4217 currency code.
# Includes all 20 currencies the product currently wants to display.
COUNTRY_TO_CURRENCY: dict[str, str] = {
    # Americas
    "US": "USD", "CA": "CAD", "MX": "MXN", "BR": "BRL",
    # Europe
    "GB": "GBP", "IE": "EUR", "DE": "EUR", "FR": "EUR", "ES": "EUR",
    "IT": "EUR", "NL": "EUR", "BE": "EUR", "AT": "EUR", "PT": "EUR",
    "GR": "EUR", "FI": "EUR", "CH": "CHF", "SE": "SEK", "NO": "NOK",
    "DK": "DKK", "PL": "PLN",
    # MEA
    "AE": "AED", "SA": "SAR", "BH": "BHD", "KW": "KWD", "QA": "QAR",
    "OM": "OMR", "EG": "EGP", "ZA": "ZAR", "NG": "NGN",
    # APAC
    "IN": "INR", "AU": "AUD", "NZ": "NZD", "SG": "SGD", "HK": "HKD",
    "JP": "JPY", "KR": "KRW", "MY": "MYR", "TH": "THB", "PH": "PHP",
    "ID": "IDR", "VN": "VND", "CN": "CNY", "TW": "TWD",
}

SUPPORTED_CURRENCIES: tuple[str, ...] = tuple(sorted({
    c for c in COUNTRY_TO_CURRENCY.values()
}))

# Display symbols. Some currencies have no widely-recognised symbol, so we
# fall back to the ISO code.
SYMBOLS: dict[str, str] = {
    "USD": "$", "CAD": "C$", "MXN": "Mex$", "BRL": "R$",
    "GBP": "£", "EUR": "€", "CHF": "CHF", "SEK": "kr", "NOK": "kr",
    "DKK": "kr", "PLN": "zł",
    "AED": "د.إ", "SAR": "﷼", "BHD": "BD", "KWD": "KD", "QAR": "QR",
    "OMR": "﷼", "EGP": "E£", "ZAR": "R", "NGN": "₦",
    "INR": "₹", "AUD": "A$", "NZD": "NZ$", "SGD": "S$", "HKD": "HK$",
    "JPY": "¥", "KRW": "₩", "MYR": "RM", "THB": "฿", "PHP": "₱",
    "IDR": "Rp", "VND": "₫", "CNY": "¥", "TWD": "NT$",
}

# Decimal places per currency (Stripe minor-units convention).
MINOR_UNITS: dict[str, int] = {
    "JPY": 0, "KRW": 0, "VND": 0, "IDR": 0,
    "BHD": 3, "KWD": 3, "OMR": 3,
    # everything else defaults to 2
}

DEFAULT_CURRENCY = "USD"

# Phone dial code -> ISO-3166 alpha-2 (covers the countries we localize for).
PHONE_PREFIX_TO_COUNTRY: dict[str, str] = {
    "+1": "US",          # US/CA share; Canada maps to CAD anyway
    "+44": "GB",
    "+91": "IN",
    "+971": "AE",
    "+966": "SA",
    "+973": "BH",
    "+965": "KW",
    "+974": "QA",
    "+968": "OM",
    "+61": "AU",
    "+64": "NZ",
    "+65": "SG",
    "+852": "HK",
    "+81": "JP",
    "+82": "KR",
    "+60": "MY",
    "+66": "TH",
    "+63": "PH",
    "+62": "ID",
    "+84": "VN",
    "+86": "CN",
    "+886": "TW",
    "+49": "DE",
    "+33": "FR",
    "+34": "ES",
    "+39": "IT",
    "+31": "NL",
    "+32": "BE",
    "+43": "AT",
    "+351": "PT",
    "+30": "GR",
    "+358": "FI",
    "+41": "CH",
    "+46": "SE",
    "+47": "NO",
    "+45": "DK",
    "+48": "PL",
    "+27": "ZA",
    "+234": "NG",
    "+20": "EG",
    "+52": "MX",
    "+55": "BR",
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

# E.164 phone matcher (loose). Captures the leading "+" and 1-4 digits.
_PHONE_RE = re.compile(r"^\+(\d{1,4})")


def _country_from_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    # Try longest prefixes first so +1... doesn't always win over +1242 etc.
    stripped = phone.strip().lstrip("0").replace(" ", "")
    if not stripped.startswith("+"):
        return None
    # Sort by length desc so we match +1242 before +1
    for prefix in sorted(PHONE_PREFIX_TO_COUNTRY.keys(), key=len, reverse=True):
        if stripped.startswith(prefix):
            return PHONE_PREFIX_TO_COUNTRY[prefix]
    m = _PHONE_RE.match(stripped)
    if not m:
        return None
    return PHONE_PREFIX_TO_COUNTRY.get(f"+{m.group(1)}")


def _normalize_query_params(qp: Mapping[str, object] | None) -> dict[str, str]:
    """Streamlit query_params returns either str or list[str]; flatten to str."""
    out: dict[str, str] = {}
    if not qp:
        return out
    for k, v in qp.items():
        if isinstance(v, list):
            out[k] = str(v[0]) if v else ""
        elif v is None:
            out[k] = ""
        else:
            out[k] = str(v)
    return out


def _currency_for_country(country: str | None) -> str:
    if not country:
        return DEFAULT_CURRENCY
    code = COUNTRY_TO_CURRENCY.get(country.upper())
    return code if code else DEFAULT_CURRENCY


def resolve_currency(
    *,
    query_params: Mapping[str, object] | None = None,
    request_headers: Mapping[str, str] | None = None,
    user_phone: str | None = None,
    explicit: str | None = None,
) -> str:
    """Pick the user's currency.

    Precedence:
      1. `explicit` (caller-provided override)
      2. `?cur=` query param
      3. CF-IPCountry / X-Country-Code header
      4. Country inferred from phone dial code
      5. "USD"
    """
    if explicit:
        c = explicit.upper()
        if c in SUPPORTED_CURRENCIES or c == DEFAULT_CURRENCY:
            return c

    qp = _normalize_query_params(query_params)
    qcur = qp.get("cur", "").upper()
    if qcur and (qcur in SUPPORTED_CURRENCIES or qcur == DEFAULT_CURRENCY):
        return qcur

    headers = request_headers or {}
    # Cloudflare sets CF-IPCountry; some proxies use X-Country-Code.
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for h in ("cf-ipcountry", "x-country-code", "x-vercel-ip-country",
              "fastly-geo-country", "cf-ipcountry-code"):
        val = lower_headers.get(h)
        if val:
            cur = _currency_for_country(val)
            if cur != DEFAULT_CURRENCY or val.upper() == "US":
                return cur

    phone_country = _country_from_phone(user_phone)
    if phone_country:
        return _currency_for_country(phone_country)

    return DEFAULT_CURRENCY


def resolve_country(
    *,
    request_headers: Mapping[str, str] | None = None,
    user_phone: str | None = None,
) -> str | None:
    """Best-effort ISO-3166 alpha-2 for the visitor."""
    headers = request_headers or {}
    lower_headers = {k.lower(): v for k, v in headers.items()}
    for h in ("cf-ipcountry", "x-country-code", "x-vercel-ip-country",
              "fastly-geo-country"):
        val = lower_headers.get(h)
        if val and len(val) == 2 and val.isalpha():
            return val.upper()
    return _country_from_phone(user_phone)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def symbol_for(currency: str) -> str:
    return SYMBOLS.get(currency.upper(), currency.upper())


def minor_units_for(currency: str) -> int:
    return MINOR_UNITS.get(currency.upper(), 2)


def round_for_currency(amount: float, currency: str) -> float:
    """Round an amount to the currency's natural precision."""
    decimals = minor_units_for(currency)
    if decimals == 0:
        return float(round(amount))
    quantum = 10 ** decimals
    return round(round(amount * quantum) / quantum, decimals)


def format_amount(amount: float, currency: str) -> str:
    """Render an amount as a human-readable string in the given currency."""
    cur = currency.upper()
    decimals = minor_units_for(cur)
    sym = symbol_for(cur)
    rounded = round_for_currency(amount, cur)
    if decimals == 0:
        body = f"{rounded:,.0f}"
    elif decimals == 3:
        body = f"{rounded:,.3f}"
    else:
        body = f"{rounded:,.2f}"
    return f"{sym}{body}"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def is_supported(currency: str) -> bool:
    return currency.upper() in SUPPORTED_CURRENCIES or currency.upper() == DEFAULT_CURRENCY


def all_currencies() -> Iterable[str]:
    return SUPPORTED_CURRENCIES
