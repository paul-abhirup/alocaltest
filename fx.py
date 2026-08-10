"""
Live FX rate fetch (USD base) with disk-friendly in-process caching.

Primary source: Frankfurter (ECB-backed, free, no key, JSON).
Fallback:      a small static table so the app never breaks if the API is
               unreachable. The static table is intentionally conservative
               and should be reviewed periodically.

Rates are cached for 6 hours to keep checkout displays stable while
remaining fresh enough to track the market.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Mapping

import requests

logger = logging.getLogger(__name__)

# Static fallback rates (USD -> X). Used if the network call fails. These are
# approximate; the live fetch always wins when available.
STATIC_RATES_USD: dict[str, float] = {
    "USD": 1.0,
    "CAD": 1.36,
    "MXN": 18.50,
    "BRL": 5.10,
    "GBP": 0.78,
    "EUR": 0.92,
    "CHF": 0.88,
    "SEK": 10.50,
    "NOK": 10.80,
    "DKK": 6.85,
    "PLN": 3.95,
    "AED": 3.67,
    "SAR": 3.75,
    "BHD": 0.38,
    "KWD": 0.31,
    "QAR": 3.64,
    "OMR": 0.38,
    "EGP": 49.50,
    "ZAR": 18.20,
    "NGN": 1500.0,
    "INR": 84.00,
    "AUD": 1.50,
    "NZD": 1.65,
    "SGD": 1.34,
    "HKD": 7.82,
    "JPY": 154.0,
    "KRW": 1370.0,
    "MYR": 4.65,
    "THB": 35.50,
    "PHP": 56.50,
    "IDR": 15800.0,
    "VND": 25400.0,
    "CNY": 7.20,
    "TWD": 32.40,
}

CACHE_TTL_SECONDS = 6 * 3600
_CACHE: dict[str, tuple[float, dict[str, float]]] = {}
_FETCH_TIMEOUT = 4.0  # seconds; don't block the UI on a slow upstream


def _fetch_live(base: str) -> dict[str, float] | None:
    """Hit Frankfurter and return a {currency: rate} dict (excluding base)."""
    url = f"https://api.frankfurter.app/latest?from={base}"
    try:
        resp = requests.get(url, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rates = data.get("rates") or {}
        rates[base] = 1.0
        if not rates:
            return None
        return {k.upper(): float(v) for k, v in rates.items()}
    except Exception as e:  # noqa: BLE001 - any failure falls back gracefully
        logger.warning("FX fetch failed (%s); using static rates", e)
        return None


def get_fx_rates(base: str = "USD") -> dict[str, float]:
    """Return USD->currency rates, cached for `CACHE_TTL_SECONDS`."""
    base = base.upper()
    now = time.monotonic()
    cached = _CACHE.get(base)
    if cached and (now - cached[0]) < CACHE_TTL_SECONDS:
        return cached[1]

    live = _fetch_live(base)
    if live:
        # Make sure the base and any supported-but-missing codes have a rate.
        merged = dict(STATIC_RATES_USD)
        merged.update(live)
        merged[base] = 1.0
        _CACHE[base] = (now, merged)
        return merged

    # Live failed -> return static (refreshed timestamp so we retry next call)
    fallback = dict(STATIC_RATES_USD)
    fallback[base] = 1.0
    # Use a half-TTL so we retry sooner if live was just down
    _CACHE[base] = (now - (CACHE_TTL_SECONDS / 2), fallback)
    return fallback


def get_rate(target: str, base: str = "USD") -> float:
    """Convenience: 1 base = X target."""
    target = target.upper()
    base = base.upper()
    rates = get_fx_rates(base)
    rate = rates.get(target)
    if rate is None:
        # Unknown target -> static lookup or 1.0
        return STATIC_RATES_USD.get(target, 1.0)
    return rate


def convert(amount: float, target: str, base: str = "USD") -> float:
    """Convert `amount` from `base` to `target`."""
    return amount * get_rate(target, base)


# Useful in tests / debugging
def _reset_cache_for_tests() -> None:
    _CACHE.clear()
