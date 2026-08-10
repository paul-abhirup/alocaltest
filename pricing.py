"""
Single source of truth for plans, credit packs, per-feature credit costs and
free-plan limits. All pricing logic in the app must read from here instead of
hardcoding values.

Currency handling:
- Every plan / pack has a `price_usd` baseline.
- For Indian visitors, a hardcoded INR price is preferred (see `INR_OVERRIDE`).
- All other currencies are derived at runtime via `price_for(...)` using FX
  rates from `fx.py`, rounded per the currency's natural precision.
"""

from currency import minor_units_for, resolve_country
from fx import convert

CYCLE_DAYS = 30

# ---------------------------------------------------------------------------
# F2F (live voice) mock interview billing
# ---------------------------------------------------------------------------
F2F_BLOCK_MINUTES = 15
F2F_BLOCK_CREDITS = 10
F2F_MAX_MINUTES_INTERVIEW_PRO = 60

# Free plan: one-time short voice interview (not a full F2F session)
FREE_VOICE_INTERVIEW_MINUTES = 3

# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------
PLANS = {
    "Career Pro": {
        "price_usd": 19.99,
        "price_inr": 699,
        "monthly_credits": 80,
        "f2f": False,
        "f2f_max_minutes": 0,
    },
    "Interview Pro": {
        "price_usd": 29.99,
        "price_inr": 1499,
        "monthly_credits": 120,
        "f2f": True,
        "f2f_max_minutes": F2F_MAX_MINUTES_INTERVIEW_PRO,
    },
    # Voucher Pro mirrors Interview Pro's allowance but excludes the ElevenLabs-
    # powered live voice interview. Granted only via voucher redemption.
    "Voucher Pro": {
        "price_usd": 0,
        "price_inr": 0,
        "monthly_credits": 120,
        "f2f": False,
        "f2f_max_minutes": 0,
        "voucher_only": True,
    },
}

FREE_PLAN = {
    "name": "Free",
    "monthly_credits": 0,
    "cv_limit": 1,
    "cl_limit": 1,
    "ats_limit": 3,
    "job_search_limit": 5,
    "voice_interview_once": True,   # one 3-minute voice interview, forever
    "f2f": False,
}

def plan_config(plan_name):
    """Return plan config dict or None."""
    return PLANS.get(plan_name)

def plan_monthly_credits(plan_name):
    cfg = plan_config(plan_name)
    return cfg["monthly_credits"] if cfg else 0

# ---------------------------------------------------------------------------
# Corporate (business) plans — legacy model routed through the same engine.
# ---------------------------------------------------------------------------
CORPORATE_PLANS = {
    "Starter":    {"credits": 500,  "duration_days": 90},
    "Growth":     {"credits": 1000, "duration_days": 90},
    "Pro":        {"credits": 2500, "duration_days": 180},
    "Plus":       {"credits": 5000, "duration_days": 180},
    "Enterprise": {"credits": 10000, "duration_days": 365},
}

def corporate_plan_config(plan_name):
    if not plan_name:
        return None
    cfg = CORPORATE_PLANS.get(plan_name)
    if cfg:
        return cfg
    # Legacy names like "Corporate Starter" → "Starter"
    stripped = plan_name
    if stripped.startswith("Corporate "):
        stripped = stripped[len("Corporate "):]
    return CORPORATE_PLANS.get(stripped)

# ---------------------------------------------------------------------------
# Interview credit packs (90-day validity)
# ---------------------------------------------------------------------------
PACKS = [
    {"name": "Quick Pack",     "credits": 30,  "price_usd": 9.99,  "price_inr": 799,  "valid_days": 90},
    {"name": "Sprint Pack",    "credits": 60,  "price_usd": 17.99, "price_inr": 1499, "valid_days": 90},
    {"name": "Intensive Pack", "credits": 100, "price_usd": 27.99, "price_inr": 2399, "valid_days": 90},
]

LEGACY_PACK_MAP = {
    "Starter Pack": "Quick Pack",
    "Pro Pack": "Sprint Pack",
    "Premium Pack": "Intensive Pack",
}

# ---------------------------------------------------------------------------
# India pricing override
# Indian visitors see hardcoded INR prices instead of USD * FX. Only the items
# listed here get the override; everything else falls through to FX.
# ---------------------------------------------------------------------------
INR_OVERRIDE: dict[str, int | float] = {
    "Career Pro":       699,
    "Interview Pro":    1499,
    "Quick Pack":       799,
    "Sprint Pack":      1499,
    "Intensive Pack":   2399,
}

JOBSQA_INR_OVERRIDE: int = 899  # JobsQA monthly in INR
JOBSQA_USD_PRICE: float = 14.99

# Corporate plans stay USD-only for now (business sales are negotiated).
CORPORATE_USD_PRICES: dict[str, int] = {
    "Starter": 149, "Growth": 299, "Pro": 449, "Plus": 699, "Enterprise": 999,
}

def pack_config(pack_name):
    target = LEGACY_PACK_MAP.get(pack_name, pack_name)
    for p in PACKS:
        if p["name"] == target:
            return p
    return None

# ---------------------------------------------------------------------------
# Per-feature credit costs
# ---------------------------------------------------------------------------
CREDIT_COSTS = {
    "cv_create": 4,              # generating a fresh CV from scratch
    "cv_tailor": 3,              # tailoring existing CV to a JD
    "cv_translate": 2,           # translating a CV
    "cl_create": 2,              # cover letter creation
    "cl_translate": 1,           # cover letter translation
    "ats": 1,                    # ATS score check (free recheck for same CV+JD pair)
    "job_search": 1,             # live job search
    "job_refresh": 1,            # refreshing a job search
    "interview_tech_qa": 3,      # technical Q&A generation
    "interview_behavioral_qa": 3,# behavioral Q&A generation
    "interview_text_mock": 6,    # text mock interview (full run)
    "interview_star": 1,         # STAR-format feedback per answer
    "interview_improve_answer": 1,  # suggested improved answer per answer
    "qa_bank_download": 4,       # downloading the Q&A bank without doing the interview
}

def credit_cost(feature):
    """Return credit cost for a feature, defaulting to 1."""
    return CREDIT_COSTS.get(feature, 1)

# ---------------------------------------------------------------------------
# Interview Q&A practice sessions — flat per-duration pricing.
# Repricing is deferred; keep the historical 5/8/12 model for now.
# ---------------------------------------------------------------------------
INTERVIEW_QA_SESSION_CREDITS = {
    "15 minutes": 5,
    "30 minutes": 8,
    "45 minutes": 12,
}

# ---------------------------------------------------------------------------
# Coupon offer handled specially by the billing page (legacy)
# ---------------------------------------------------------------------------
SPECIAL_OFFER_CODE = "PREMIUM599"


# ---------------------------------------------------------------------------
# Currency-aware pricing
# ---------------------------------------------------------------------------
def _round_for(amount: float, currency: str) -> float:
    """Round an amount to the currency's natural precision (no FX)."""
    decimals = minor_units_for(currency)
    if decimals == 0:
        return float(round(amount))
    quantum = 10 ** decimals
    return round(round(amount * quantum) / quantum, decimals)


def price_for(
    cfg: dict,
    currency: str,
    *,
    country: str | None = None,
    name_key: str = "name",
) -> tuple[float, str]:
    """Return (amount, currency_code) for a plan/pack config in `currency`.

    Args:
        cfg: Plan or pack dict (must have `price_usd`).
        currency: Target ISO-4217 currency code (e.g. "EUR", "JPY", "INR").
        country: ISO-3166 alpha-2 country code. If "IN" and the item has an
            INR override, the hardcoded price is used.
        name_key: Dict key that holds the display name (used to look up
            INR override). Defaults to "name".

    Returns:
        (amount, currency_code) - amount is rounded to the currency's
        natural precision.
    """
    cur = (currency or "USD").upper()
    name = cfg.get(name_key)

    # India override
    if (country or "").upper() == "IN" and name and name in INR_OVERRIDE:
        return float(INR_OVERRIDE[name]), "INR"

    amount_usd = float(cfg.get("price_usd", 0.0))
    if cur == "USD" or amount_usd == 0.0:
        return _round_for(amount_usd, cur), cur

    converted = convert(amount_usd, cur, base="USD")
    return _round_for(converted, cur), cur


def price_for_jobsqa(currency: str, *, country: str | None = None) -> tuple[int, str]:
    """JobsQA-specific pricing (single product)."""
    cur = (currency or "USD").upper()
    if (country or "").upper() == "IN":
        return JOBSQA_INR_OVERRIDE, "INR"
    if cur == "USD":
        # Stripe wants minor units (cents)
        return int(round(JOBSQA_USD_PRICE * 100)), "USD"
    amount = convert(JOBSQA_USD_PRICE, cur, base="USD")
    return int(round(amount * (10 ** minor_units_for(cur)))), cur


def country_from(*, request_headers=None, user_phone: str | None = None) -> str | None:
    """Convenience wrapper around `currency.resolve_country`."""
    return resolve_country(request_headers=request_headers, user_phone=user_phone)
