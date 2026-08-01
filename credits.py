import logging
from credit_engine import has_enough, spend_credits, wallet_balance
from database import record_credit_usage

logger = logging.getLogger(__name__)

def check_user_access(email, required_credits=2, account_type="individual", feature=None):
    """Check if user has sufficient credits via credit_engine."""
    try:
        return has_enough(account_type, email, amount=required_credits, feature=feature)
    except Exception as e:
        logger.exception(f"Error checking user access for {email}: {e}")
        return False


def deduct_user_credits(email, amount, feature=None, account_type="individual"):
    """Deduct credits for individual or business users via credit_engine."""
    try:
        res = spend_credits(account_type, email, feature or "general", amount=amount)
        return res.get("ok", False)
    except Exception as e:
        logger.exception(f"Error deducting credits for {email}: {e}")
        return False


def get_credit_balance(email, account_type="individual"):
    """Get current credit balance via credit_engine."""
    try:
        bal = wallet_balance(account_type, email)
        return bal.get("total", 0)
    except Exception as e:
        logger.exception(f"Error getting credit balance for {email}: {e}")
        return 0

