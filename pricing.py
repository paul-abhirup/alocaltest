"""
Single source of truth for plans, credit packs, per-feature credit costs and
free-plan limits. All pricing logic in the app must read from here instead of
hardcoding values.
"""

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
