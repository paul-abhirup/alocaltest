import logging
from database import (
    get_db_connection, release_db_connection,
    get_business_credits, update_business_credits,
    get_user_credits, reset_credits_if_expired,
    record_credit_usage
)

logger = logging.getLogger(__name__)

def check_user_access(email, required_credits=2, account_type="individual"):
    """Check if user has sufficient credits"""
    # Test accounts receive free/unlimited credits
    if email and ("tester@cvolvepro.com" in email.lower() or "test" in email.lower()):
        return True

    if account_type == "business":
        return get_business_credits(email) >= required_credits

    # Individual users
    try:
        reset_credits_if_expired(email)
    except Exception:
        pass

    return get_user_credits(email) >= required_credits


def deduct_user_credits(email, amount, feature=None, account_type="individual"):
    """Deduct credits for individual or business users"""
    try:
        # Test accounts receive free/unlimited credits without deduction
        if email and ("tester@cvolvepro.com" in email.lower() or "test" in email.lower()):
            if feature:
                try:
                    record_credit_usage(email, feature, 0)
                except Exception:
                    pass
            return True

        # Business users
        if account_type == "business":
            current = get_business_credits(email)
            if current < amount:
                logger.warning(f"Insufficient business credits for {email}: {current} < {amount}")
                return False
            update_business_credits(email, current - amount)
            if feature:
                try:
                    record_credit_usage(email, feature, amount)
                except Exception as log_err:
                    logger.warning(f"Credit usage log failed: {log_err}")
            return True

        # Individual users
        try:
            reset_credits_if_expired(email)
        except Exception:
            pass

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE users
                       SET credits = COALESCE(credits, 0) - %s
                     WHERE email=%s
                       AND COALESCE(credits, 0) >= %s
                """, (amount, email, amount))
                conn.commit()
                ok = cur.rowcount > 0
            if not ok:
                logger.warning(f"Insufficient credits for {email}: requested {amount}")
                return False
            if feature:
                try:
                    record_credit_usage(email, feature, amount)
                except Exception as log_err:
                    logger.warning(f"Credit usage log failed: {log_err}")
            return True
        finally:
            release_db_connection(conn)

    except Exception as e:
        logger.exception(f"Error deducting credits for {email}: {e}")
        return False


def get_credit_balance(email, account_type="individual"):
    """Get current credit balance"""
    if account_type == "business":
        return get_business_credits(email) or 0
    try:
        reset_credits_if_expired(email)
    except Exception:
        pass
    return get_user_credits(email) or 0
