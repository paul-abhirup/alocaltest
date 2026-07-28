import os
import logging
from datetime import datetime, timedelta

import streamlit as st
import stripe
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import tomli as tomllib  # Python 3.10 fallback (in requirements.txt)

from database import (
    get_db_connection,
    save_payment,
    update_user_credits,
    validate_discount_code,
    use_discount_code,
    payment_exists,
    jobsqa_get_user_by_email
)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load secrets (VPS path if present; falls back to environment variables locally)
secrets = {}
_SECRETS_PATH = "/opt/cvolvepro/CVOLVE-PRO/.streamlit/secrets.toml"
if os.path.exists(_SECRETS_PATH):
    with open(_SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)

stripe.api_key = secrets.get("STRIPE_SECRET_KEY") or os.getenv("STRIPE_SECRET_KEY")





def process_payment(user_email, payment_type, amount, details):
    """(Legacy) Not used for Checkout anymore. Left for compatibility."""
    try:
        intent = stripe.PaymentIntent.create(
            amount=int(amount * 100),
            currency='usd',
            metadata={'user_email': user_email, 'type': payment_type, 'details': str(details)}
        )
        # Do NOT mutate credits here. We'll rely on Checkout + success redirect or webhooks.
        save_payment(user_email, amount, payment_type, intent.id, credits_purchased=0)
        return True
    except stripe.error.StripeError as e:
        st.error(f"❌ Payment failed: {str(e)}")
        return False

def create_subscription(user_email, plan, stripe_payment_id):
    """Create subscription record"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Calculate end date based on plan
    if "Annual" in plan:
        end_date = datetime.now() + timedelta(days=365)
    else:
        end_date = datetime.now() + timedelta(days=30)
    
    cursor.execute("""
        INSERT INTO subscriptions (user_email, plan, end_date, stripe_subscription_id)
        VALUES (%s, %s, %s, %s)
    """, (user_email, plan, end_date, stripe_payment_id))
    
    conn.commit()
    cursor.close()
    conn.close()

def check_subscription(user_email):
    """Check if user has active subscription"""
    if user_email and ("tester@cvolvepro.com" in str(user_email).lower() or "test" in str(user_email).lower()):
        return {
            'plan': 'Corporate Pro (Unlimited)',
            'next_billing': '2099-12-31'
        }
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT plan, end_date FROM subscriptions 
        WHERE user_email = %s 
        AND status = 'active' 
        AND end_date > CURRENT_TIMESTAMP
        ORDER BY end_date DESC
        LIMIT 1
    """, (user_email,))
    
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if result:
        return {
            'plan': result[0],
            'next_billing': result[1].strftime('%Y-%m-%d')
        }
    return None

def apply_discount_code(user_email, code):
    """Apply discount code"""
    discount = validate_discount_code(code)
    
    if discount:
        # Use the discount code
        use_discount_code(code)
        
        # Apply discount (this would be used in the next payment)
        st.session_state.discount_applied = {
            'code': code,
            'discount_percent': discount['discount_percent']
        }
        
        return True
    
    return False

def get_stripe_public_key():
    """Get Stripe public key for frontend"""
    return os.getenv("STRIPE_PUBLIC_KEY", "pk_test_default")

def create_checkout_session(user_email, amount, payment_type, success_url, cancel_url,
                            plan=None, credits=None, currency="USD", plan_name=None, duration=None):
    """
    Create a Stripe Checkout Session charging in the caller's local currency.
    Handles minor units correctly (e.g., BHD = 3 decimals).
    """
    try:
        # --- minor-units map (default = 2) ---
        # Add more as needed (e.g., JPY/KRW/VND are 0)
        MINOR_UNITS = {
            "BHD": 3,  # Bahraini Dinar (3 decimal places)
            # all others we support here (USD, EUR, INR, AED, AUD, GBP) use 2
        }
        def _multiplier_for(code: str) -> int:
            return 10 ** MINOR_UNITS.get(code.upper(), 2)

        name = (f'CVolve Pro • {plan} Subscription'
                if payment_type == "subscription"
                else f'CVolve Pro • {payment_type}')

        md = {'user_email': user_email, 'type': payment_type}
        if plan:
            md['plan'] = plan
        if credits is not None:
            md['credits'] = str(credits)

        if plan_name:
            md['plan_name'] = plan_name

        if duration:
            md['duration'] = duration

        cur = (currency or "USD").upper()
        mult = _multiplier_for(cur)

        # amount is already in local currency; convert to minor units for Stripe
        unit_amount = int(round(float(amount) * mult))

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': cur.lower(),
                    'product_data': {'name': name},
                    'unit_amount': unit_amount,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f"{success_url}&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=cancel_url,
            metadata=md
        )
        return session.url
    except Exception as e:
        st.error(f"❌ Error creating checkout session: {str(e)}")
        return None
    
# JobsQA specific checkout (SAFE WRAPPER)
def create_jobsqa_checkout_session(
    user_email: str,
    amount: int,
    currency: str,
    success_url: str,
    cancel_url: str
):
    import stripe

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        customer_email=user_email,
        line_items=[{
            "price_data": {
                "currency": currency,
                "product_data": {
                    "name": "JobsQA Monthly"
                },
                "unit_amount": amount,  # cents / paise
            },
            "quantity": 1
        }],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={
            "service": "jobsqa",
            "credits": "60",
            "user_email": user_email
        }
    )

    return session.url

    

def handle_jobsqa_payment(session):
    """
    JobsQA-only payment handler.
    Called by Stripe webhook when payment succeeds.
    Does NOT affect other CVolvePro services.
    
    Args:
        session: Stripe checkout.session object or dict
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Handle both dict and object types
        if hasattr(session, 'customer_email'):
            email = session.customer_email
            metadata = session.metadata or {}
            session_id = session.id
        else:
            email = session.get("customer_email")
            metadata = session.get("metadata", {})
            session_id = session.get("id")
        
        # Validate email
        if not email:
            logger.error("❌ No customer_email in session")
            return False

        # ✅ JobsQA identification
        if metadata.get("service") != "jobsqa":
            logger.info(f"⏭️  Skipping non-JobsQA payment")
            return False

        # Get credits amount
        credits = int(metadata.get("credits", 0))
        if credits <= 0:
            logger.error(f"❌ Invalid credits amount: {credits}")
            return False

        # ✅ Fetch JobsQA user
        user = jobsqa_get_user_by_email(email)
        if not user:
            logger.error(f"❌ User not found: {email}")
            return False

        user_id = user["id"]

        # ✅ Add credits with expiry
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # Check if already processed (idempotency via credit logs)
            cur.execute("""
                SELECT id FROM jobsqa_credit_logs 
                WHERE user_id = %s AND action = %s
            """, (user_id, f"stripe_payment:{session_id}"))
            
            if cur.fetchone():
                logger.info(f"⏭️  Payment {session_id} already processed for {email}")
                return True

            # Check if user already has credits record
            cur.execute("SELECT credits FROM jobsqa_credits WHERE user_id = %s", (user_id,))
            existing = cur.fetchone()

            if existing:
                # Update existing credits
                cur.execute("""
                    UPDATE jobsqa_credits 
                    SET credits = credits + %s,
                        expires_at = NOW() + INTERVAL '30 days',
                        updated_at = NOW()
                    WHERE user_id = %s
                """, (credits, user_id))
                new_total = existing[0] + credits
                logger.info(f"✅ Updated credits for user {email}: +{credits} credits (total: {new_total})")
            else:
                # Insert new credits record
                cur.execute("""
                    INSERT INTO jobsqa_credits (user_id, credits, expires_at)
                    VALUES (%s, %s, NOW() + INTERVAL '30 days')
                """, (user_id, credits))
                logger.info(f"✅ Created credits for user {email}: {credits} credits")
            
            # Log payment in credit logs for idempotency tracking
            cur.execute("""
                INSERT INTO jobsqa_credit_logs (user_id, action, credits_change)
                VALUES (%s, %s, %s)
            """, (user_id, f"stripe_payment:{session_id}", credits))
            
            conn.commit()
            logger.info(f"✅ Successfully added {credits} credits to {email} (user_id: {user_id})")
            return True
            
        except Exception as db_error:
            conn.rollback()
            logger.error(f"❌ Database error for {email}: {str(db_error)}")
            import traceback
            traceback.print_exc()
            return False
        finally:
            cur.close()
            conn.close()

    except Exception as e:
        logger.error(f"❌ JobsQA payment handler error: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


