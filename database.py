import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
import json
import logging
import threading
from datetime import datetime, timedelta
import hashlib
import secrets
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Fixed key so all app instances serialize schema migrations through the same
# advisory lock (avoids cross-process deadlocks between concurrent init_db runs
# and between DDL and in-flight queries).
_INIT_DB_ADVISORY_KEY = 825379421

# Connection pool
_connection_pool = None
_pool_lock = threading.Lock()

def _init_connection_pool():
    """Initialize connection pool (called once)"""
    global _connection_pool
    with _pool_lock:
        if _connection_pool is not None:
            return _connection_pool
        
        db_url = get_secret("DATABASE_URL") or get_secret("POSTGRES_URL")
        if db_url:
            if db_url.startswith("postgres://"):
                db_url = db_url.replace("postgres://", "postgresql://", 1)
            _connection_pool = pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=db_url,
                connect_timeout=10,
                application_name="cvolvepro"
            )
        return _connection_pool

def release_db_connection(conn):
    """Return connection to pool"""
    pool = _init_connection_pool()
    if pool:
        pool.putconn(conn)

def _search_container(container, key: str):
    """Safely search key in dict/AttrDict/Secrets object, case-insensitively."""
    if container is None:
        return None
    try:
        if key in container:
            val = container[key]
            if val is not None and str(val).strip() != "":
                return str(val).strip()
    except Exception:
        pass

    try:
        target_lower = key.lower()
        keys_iter = container.keys() if hasattr(container, "keys") else iter(container)
        for k in keys_iter:
            if str(k).lower() == target_lower:
                val = container[k]
                if val is not None and str(val).strip() != "":
                    return str(val).strip()
    except Exception:
        pass

    try:
        if hasattr(container, key):
            val = getattr(container, key)
            if val is not None and not callable(val) and str(val).strip() != "":
                return str(val).strip()
    except Exception:
        pass

    return None

def get_secret(key: str, default=None):
    """
    Exhaustively fetch a secret from Streamlit secrets (st.secrets) or OS environment variables.
    """
    possible_keys = [key, key.lower(), key.upper()]
    if key.startswith("DB_"):
        short_key = key[3:]
        possible_keys.extend([short_key, short_key.lower(), short_key.upper()])
        if short_key.lower() == "name":
            possible_keys.extend(["dbname", "database"])

    # 1. Streamlit Secrets search
    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets is not None:
            for pk in possible_keys:
                val = _search_container(st.secrets, pk)
                if val is not None:
                    return val

            sections = ["postgres", "postgresql", "database", "db"]
            for sec_name in sections:
                sec = _search_container(st.secrets, sec_name)
                if sec is not None:
                    try:
                        sub_container = st.secrets[sec_name]
                        for pk in possible_keys:
                            val = _search_container(sub_container, pk)
                            if val is not None:
                                return val
                    except Exception:
                        pass

            try:
                if "connections" in st.secrets:
                    conns = st.secrets["connections"]
                    for pg_sec in ["postgresql", "postgres"]:
                        if pg_sec in conns:
                            for pk in possible_keys:
                                val = _search_container(conns[pg_sec], pk)
                                if val is not None:
                                    return val
            except Exception:
                pass
    except Exception:
        pass

    # 2. Environment variables search
    for pk in possible_keys:
        val = os.getenv(pk)
        if val is not None and val.strip() != "":
            return val.strip()

    # 3. Postgres Standard Environment Variables
    pg_env_map = {
        "DB_HOST": ["PGHOST", "POSTGRES_HOST"],
        "DB_PORT": ["PGPORT", "POSTGRES_PORT"],
        "DB_NAME": ["PGDATABASE", "POSTGRES_DB"],
        "DB_USER": ["PGUSER", "POSTGRES_USER"],
        "DB_PASSWORD": ["PGPASSWORD", "POSTGRES_PASSWORD"],
        "DB_SSLMODE": ["PGSSLMODE", "POSTGRES_SSLMODE"]
    }
    if key in pg_env_map:
        for alt_env in pg_env_map[key]:
            val = os.getenv(alt_env)
            if val is not None and val.strip() != "":
                return val.strip()

    return default

def get_db_config_summary():
    """Return dict of current DB connection resolution for diagnostics (passwords masked)."""
    db_url = get_secret("DATABASE_URL") or get_secret("POSTGRES_URL") or get_secret("DB_URL")
    if db_url:
        masked_url = db_url
        if "@" in masked_url and ":" in masked_url:
            try:
                pre, post = masked_url.rsplit("@", 1)
                proto, creds = pre.split("://", 1)
                if ":" in creds:
                    u, _ = creds.split(":", 1)
                    masked_url = f"{proto}://{u}:****@{post}"
            except Exception:
                masked_url = "DATABASE_URL provided (password masked)"
        return {"connection_mode": "URL", "url": masked_url}

    host = get_secret("DB_HOST", "127.0.0.1")
    return {
        "connection_mode": "parameters",
        "host": host,
        "port": get_secret("DB_PORT", "5432"),
        "database": get_secret("DB_NAME", get_secret("DB_DATABASE", "cvolvepro")),
        "user": get_secret("DB_USER", get_secret("DB_USERNAME", "postgres")),
        "sslmode": get_secret("DB_SSLMODE", "require" if host not in ("127.0.0.1", "localhost") else None),
        "is_default_localhost": host in ("127.0.0.1", "localhost")
    }

def get_db_connection():
    """Get connection from pool"""
    pool = _init_connection_pool()
    if pool:
        return pool.getconn()
    # Fallback to direct connection (should not happen in production)
    db_url = get_secret("DATABASE_URL") or get_secret("POSTGRES_URL") or get_secret("DB_URL")
    if db_url:
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg2.connect(db_url, connect_timeout=10, application_name="cvolvepro")

    host = get_secret("DB_HOST", "127.0.0.1")
    port = int(get_secret("DB_PORT", "5432"))
    database = get_secret("DB_NAME", get_secret("DB_DATABASE", "cvolvepro"))
    user = get_secret("DB_USER", get_secret("DB_USERNAME", "postgres"))
    password = get_secret("DB_PASSWORD", "")
    sslmode = get_secret("DB_SSLMODE", None)

    conn_kwargs = {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        "connect_timeout": 10,
        "application_name": "cvolvepro"
    }

    if sslmode:
        conn_kwargs["sslmode"] = sslmode
    elif host not in ("127.0.0.1", "localhost"):
        conn_kwargs["sslmode"] = "require"

    return psycopg2.connect(**conn_kwargs)

def _init_db_once():
    """Initialize database tables.

    Runs under a cross-process advisory lock with a bounded lock_timeout so that
    concurrent app starts (or a start overlapping in-flight queries) serialize
    instead of deadlocking on AccessExclusiveLock vs AccessShareLock.
    """
    conn = get_db_connection()
    conn.rollback()  # clear any stale transaction state from a pooled connection
    cursor = conn.cursor()

    # Bound how long DDL waits for a conflicting runtime lock, then retry
    # (init_db) instead of letting the server raise a deadlock.
    try:
        cursor.execute("SET LOCAL lock_timeout = '20000'")
    except Exception:
        pass
    # Serialize DDL across all app instances / processes.
    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_INIT_DB_ADVISORY_KEY,))


    # =========================================================
    # BUSINESS USERS TABLE
    # =========================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_users (
            id SERIAL PRIMARY KEY,

            company_name VARCHAR(255) NOT NULL,
            owner_name VARCHAR(255) NOT NULL,

            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,

            business_type VARCHAR(100),

            plan_name VARCHAR(100),
            credits INTEGER DEFAULT 0,

            subscription_duration VARCHAR(50),

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login TIMESTAMP,

            is_active BOOLEAN DEFAULT TRUE
        )
    """)
    # Active business plan tracking (may predate this migration on prod)
    cursor.execute("""
        ALTER TABLE business_users
        ADD COLUMN IF NOT EXISTS current_plan VARCHAR(100)
    """)
    cursor.execute("""
        ALTER TABLE business_users
        ADD COLUMN IF NOT EXISTS plan_expiry TIMESTAMP
    """)

    # =========================================================
    # BUSINESS SUBSCRIPTIONS
    # =========================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_subscriptions (
            id SERIAL PRIMARY KEY,

            business_email VARCHAR(255),

            plan_name VARCHAR(100),
            credits INTEGER,

            amount DECIMAL(10,2),

            duration VARCHAR(50),

            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,

            status VARCHAR(50) DEFAULT 'active'
        )
    """)

    # =========================================================
    # BUSINESS CREDIT USAGE
    # =========================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_credit_usage (
            id SERIAL PRIMARY KEY,

            business_email VARCHAR(255),

            feature VARCHAR(100),

            credits_used INTEGER,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        email VARCHAR(255) UNIQUE NOT NULL,
        phone VARCHAR(20),
        auth_provider VARCHAR(50) NOT NULL,
        password_hash VARCHAR(255),
        is_verified BOOLEAN DEFAULT FALSE,
        verification_token VARCHAR(64),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP,
        credits INTEGER DEFAULT 5,
        total_cvs_generated INTEGER DEFAULT 0,
        avg_ats_score FLOAT DEFAULT 0.0,
        otp_expires_at TIMESTAMP
        );

    """)
    
    # Subscriptions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            plan VARCHAR(50) NOT NULL,
            status VARCHAR(20) DEFAULT 'active',
            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            end_date TIMESTAMP,
            stripe_subscription_id VARCHAR(255),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # CV generations table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cv_generations (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            job_description TEXT,
            original_resume TEXT,
            generated_cv TEXT,
            template_used VARCHAR(50),
            ats_score INTEGER,
            target_match INTEGER,
            processing_time FLOAT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # User sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            session_data JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Needed so ON CONFLICT (user_email) works in save_user_session
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_user_sessions_email ON user_sessions(user_email)")    
    
    # Payments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            amount DECIMAL(10, 2) NOT NULL,
            type VARCHAR(20) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            stripe_payment_id VARCHAR(255),
            credits_purchased INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Enforce idempotency when a Stripe ID exists (allow multiple NULLs)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_stripe_id
        ON payments(stripe_payment_id) WHERE stripe_payment_id IS NOT NULL
    """)
    
    # Discount codes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS discount_codes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            discount_percent INTEGER NOT NULL,
            max_uses INTEGER DEFAULT 1,
            current_uses INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_special_discounts (
            id SERIAL PRIMARY KEY,

            email VARCHAR(255) NOT NULL,

            plan_name VARCHAR(255) NOT NULL,

            discount_percent INTEGER NOT NULL,

            is_active BOOLEAN DEFAULT TRUE,

            start_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

            end_date TIMESTAMP,

            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # user coupon usage table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_coupon_usage (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            coupon_code VARCHAR(50),
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Credit usage table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_usage (
            id SERIAL PRIMARY KEY,
            user_email VARCHAR(255) REFERENCES users(email),
            feature VARCHAR(50) NOT NULL,   -- 'CV', 'CL', 'Interview QA', 'ATS'
            credits INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Monthly credit cycle anchor (starts when a plan is purchased)
    cursor.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS credit_cycle_start TIMESTAMP
    """)

    # =========================================================
    # CREDIT ENGINE TABLES (plans, packs, ledger)
    # =========================================================

    # Wallet = single source of truth for credit balances per account.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_wallets (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',  -- individual | business
            email VARCHAR(255) NOT NULL,
            plan VARCHAR(50) DEFAULT 'Free',
            subscription_credits INTEGER DEFAULT 0,
            pack_credits INTEGER DEFAULT 0,
            cycle_start TIMESTAMP,
            next_renewal TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (account_type, email)
        )
    """)

    # Immutable ledger of every credit movement.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            email VARCHAR(255) NOT NULL,
            feature VARCHAR(80),
            amount INTEGER NOT NULL,            -- signed delta
            txn_type VARCHAR(30) NOT NULL,      -- charge|refund|plan_purchase|pack_purchase|expire|renewal|credit
            source VARCHAR(20),                 -- subscription | pack | free | gift
            pack_id INTEGER,                    -- pack debited (for pack-source charges)
            balance_after INTEGER NOT NULL DEFAULT 0,
            request_id VARCHAR(64),
            idempotency_key VARCHAR(128),
            group_id VARCHAR(64),
            reference_txn_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Newer column added after initial migration (safe on fresh + existing DBs).
    cursor.execute("ALTER TABLE credit_transactions ADD COLUMN IF NOT EXISTS group_id VARCHAR(64)")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS ix_credit_tx_group
        ON credit_transactions(group_id) WHERE group_id IS NOT NULL
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_tx_request
        ON credit_transactions(request_id) WHERE request_id IS NOT NULL
    """)
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_credit_tx_idem
        ON credit_transactions(idempotency_key) WHERE idempotency_key IS NOT NULL
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS ix_credit_tx_account
        ON credit_transactions(account_type, email, created_at DESC)
    """)

    # Individual purchased credit packs (90-day validity).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS credit_packs (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            email VARCHAR(255) NOT NULL,
            pack_name VARCHAR(100) NOT NULL,
            credits INTEGER NOT NULL,
            credits_remaining INTEGER NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            stripe_session_id VARCHAR(255),
            purchased_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ATS hash-pair history → free rechecks for an identical CV+JD pair.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ats_checks (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            email VARCHAR(255) NOT NULL,
            cv_hash VARCHAR(64) NOT NULL,
            jd_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (account_type, email, cv_hash, jd_hash)
        )
    """)

    # Free-plan usage counters (reset monthly).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS free_usage_counters (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            email VARCHAR(255) NOT NULL,
            feature VARCHAR(50) NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            period_start TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (account_type, email, feature, period_start)
        )
    """)

    # Live F2F mock-interview sessions (incremental block billing).
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS f2f_sessions (
            id SERIAL PRIMARY KEY,
            account_type VARCHAR(20) NOT NULL DEFAULT 'individual',
            email VARCHAR(255) NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            blocks_charged INTEGER NOT NULL DEFAULT 0,
            blocks_charged_minutes INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) DEFAULT 'active',  -- active | ended | refunded
            is_free BOOLEAN DEFAULT FALSE,        -- free-plan one-time 3-min voice interview
            max_minutes INTEGER DEFAULT 0,        -- session cap (0 = unlimited)
            last_charge_txn_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)




    
    conn.commit()
    cursor.close()
    conn.close()


def init_db(retries: int = 5, base_delay: float = 1.0):
    """Initialize database tables, retrying transient lock/deadlock errors.

    A single DDL statement (ALTER TABLE / CREATE INDEX) takes an
    AccessExclusiveLock; if it overlaps an in-flight query that holds locks on
    another table, PostgreSQL may abort one side with 'deadlock detected'. We
    retry the migration a few times so a transient overlap does not fail startup.
    """
    last_err = None
    for attempt in range(retries):
        try:
            _init_db_once()
            return
        except psycopg2.OperationalError as e:
            last_err = e
            # 40P01 = deadlock_detected, 55P03 = lock_not_available
            if e.pgcode not in ("40P01", "55P03"):
                raise
            delay = base_delay * (attempt + 1)
            logger.warning(
                "init_db transient lock error (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, retries, e, delay,
            )
            time.sleep(delay)
    if last_err is not None:
        raise last_err


def get_user_data(email):
    """Get user data by email"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT * FROM users WHERE email = %s
    """, (email,))
    
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return dict(user) if user else None

def create_user(email, name, auth_provider, password_hash=None):
    """Create new user"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO users (email, name, auth_provider, password_hash, last_login)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (email) DO UPDATE SET
        last_login = EXCLUDED.last_login
        RETURNING *
    """, (email, name, auth_provider, password_hash, datetime.now()))
    
    user = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    
    return user


def create_business_user(
    company_name,
    owner_name,
    email,
    password_hash,
    plan_name
):

    conn = get_db_connection()
    cursor = conn.cursor()

    plans = {
        "Corporate Starter": (500, "3 months"),
        "Corporate Growth": (1000, "3 months"),
        "Corporate Pro": (2500, "6 months"),
        "Corporate Plus": (5000, "6 months"),
        "Corporate Advanced": (7500, "1 year"),
        "Corporate Enterprise": (10000, "1 year")
    }

    credits, duration = plans[plan_name]

    duration_days = {"3 months": 90, "6 months": 180}.get(duration.lower(), 365)

    cursor.execute("""
        INSERT INTO business_users (
            company_name,
            owner_name,
            email,
            password_hash,
            plan_name,
            credits,
            subscription_duration,
            current_plan,
            plan_expiry
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                CURRENT_TIMESTAMP + %s * INTERVAL '1 day')
    """, (
        company_name,
        owner_name,
        email,
        password_hash,
        plan_name,
        credits,
        duration,
        plan_name,
        duration_days,
    ))

    # Seed the credit-engine wallet so balance displays consistently.
    cursor.execute("""
        INSERT INTO credit_wallets
            (account_type, email, plan, subscription_credits, pack_credits,
             cycle_start, next_renewal)
        VALUES ('business', %s, %s, %s, 0, CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP + %s * INTERVAL '1 day')
        ON CONFLICT (account_type, email) DO NOTHING
    """, (email, plan_name, credits, duration_days))

    conn.commit()
    cursor.close()
    conn.close()



def get_business_user(email):

    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT * FROM business_users
        WHERE email=%s
    """, (email,))

    user = cursor.fetchone()

    cursor.close()
    conn.close()

    return dict(user) if user else None

def authenticate_business_user(email, password):

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        cur.execute("""
            SELECT * FROM business_users
            WHERE email = %s
        """, (email.strip().lower(),))

        user = cur.fetchone()

        if not user:
            return None

        if check_password_hash(
            user["password_hash"],
            password
        ):
            return user

        return None

    finally:
        cur.close()
        conn.close()



def update_user_credits(email, credits):
    """Increment credits (works even if credits is NULL)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
           SET credits = COALESCE(credits, 0) + %s 
         WHERE email=%s
    """, (credits, email))
    conn.commit(); cur.close(); conn.close()


def get_user_credits(email):
    """Get user's current credits (auto-resets cycle if expired)."""
    try:
        reset_credits_if_expired(email)
    except Exception:
        pass

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(credits, 0) FROM users WHERE email=%s", (email,))
    row = cur.fetchone()
    cur.close(); conn.close()
    return int(row[0]) if row else 0



def save_cv_generation(user_email, job_description, original_resume, generated_cv, template_used, ats_score, target_match, processing_time):
    """Save CV generation record"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO cv_generations (user_email, job_description, original_resume, generated_cv, template_used, ats_score, target_match, processing_time)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (user_email, job_description, original_resume, generated_cv, template_used, ats_score, target_match, processing_time))
    
    # Update user stats
    cursor.execute("""
        UPDATE users SET 
        total_cvs_generated = total_cvs_generated + 1,
        avg_ats_score = (
            SELECT AVG(ats_score) FROM cv_generations WHERE user_email = %s
        )
        WHERE email = %s
    """, (user_email, user_email))
    
    conn.commit()
    cursor.close()
    conn.close()

def save_user_session(user_email, session_data):
    """Save user session data for auto-save"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO user_sessions (user_email, session_data)
        VALUES (%s, %s)
        ON CONFLICT (user_email) DO UPDATE SET
        session_data = EXCLUDED.session_data,
        updated_at = CURRENT_TIMESTAMP
    """, (user_email, json.dumps(session_data)))

    conn.commit()
    cursor.close()
    conn.close()


def get_user_session(user_email):
    """Get user session data"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT session_data FROM user_sessions WHERE user_email = %s
    """, (user_email,))

    result = cursor.fetchone()
    cursor.close()
    conn.close()

    if not result or result[0] is None:
        return {}
    data = result[0]
    # jsonb columns are auto-decoded to dict/list by psycopg2; a text column (or
    # legacy rows) come back as a JSON string that still needs parsing.
    if isinstance(data, (dict, list)):
        return data
    return json.loads(data)


def save_alignment_answers(user_email, jd_hash, gaps, answers):
    """Phase 2 CV↔JD alignment: persist follow-up answers in the user_sessions JSON blob,
    namespaced by JD hash so the same verified answers enrich CV, cover letter, and interview
    prep for that JD. Read-modify-write to preserve other keys in the blob.

    NOTE (UI wiring): the auto-save path (app.py) writes the whole blob wholesale — when wired,
    it must merge rather than overwrite, or store alignment inside the same auto_save dict, so
    these answers aren't clobbered.
    """
    session = get_user_session(user_email) or {}
    alignment = session.get("alignment") or {}
    alignment[jd_hash] = {
        "gaps": gaps or [],
        "answers": {k: v for k, v in (answers or {}).items() if v and str(v).strip()},
        "updated_at": datetime.utcnow().isoformat(),
    }
    session["alignment"] = alignment
    save_user_session(user_email, session)


def get_alignment_answers(user_email, jd_hash):
    """Return {'gaps':[...], 'answers':{...}, 'updated_at':...} for a JD hash, or {} if none."""
    session = get_user_session(user_email) or {}
    return (session.get("alignment") or {}).get(jd_hash, {})


def save_payment(user_email, amount, payment_type, stripe_payment_id, credits_purchased=0):
    """Save payment record"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO payments (user_email, amount, type, stripe_payment_id, credits_purchased)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_email, amount, payment_type, stripe_payment_id, credits_purchased))
    
    conn.commit()
    cursor.close()
    conn.close()

def create_discount_code(code, discount_percent, max_uses=1, expires_at=None):
    """Create discount code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO discount_codes (code, discount_percent, max_uses, expires_at)
        VALUES (%s, %s, %s, %s)
    """, (code, discount_percent, max_uses, expires_at))
    
    conn.commit()
    cursor.close()
    conn.close()


def seed_discount_codes():
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO discount_codes (code, discount_percent, max_uses)
        VALUES ('ANALYTICSWITHANAND', 5, 1000)
        ON CONFLICT (code) DO NOTHING
    """)
    cur.execute("""
        INSERT INTO discount_codes (code, discount_percent, max_uses)
        VALUES ('IWD20', 20, 1000)
        ON CONFLICT (code) DO NOTHING
    """)
    cur.execute("""
    INSERT INTO discount_codes (code, discount_percent, max_uses)
    VALUES ('PREMIUM599', 0, 1000)
    ON CONFLICT (code) DO NOTHING
    """)
    cur.execute("""
    INSERT INTO discount_codes (code, discount_percent, max_uses)
    VALUES ('CVOLVE40', 40, 1000)
    ON CONFLICT (code) DO NOTHING
    """)

    cur.execute("""
    INSERT INTO discount_codes (code, discount_percent, max_uses)
    VALUES ('HFPI10', 10, 1000)
    ON CONFLICT (code) DO NOTHING
    """)

    cur.execute("""
    INSERT INTO discount_codes (code, discount_percent, max_uses)
    VALUES ('PROSAVITRI', 0, 1000)
    ON CONFLICT (code) DO NOTHING
    """)
    conn.commit(); cur.close(); conn.close()


def validate_discount_code(code):
    """Validate discount code"""
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    cursor.execute("""
        SELECT * FROM discount_codes 
        WHERE code = %s 
        AND current_uses < max_uses 
        AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)
    """, (code,))
    
    discount = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return dict(discount) if discount else None

def use_discount_code(code):
    """Use discount code"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE discount_codes 
        SET current_uses = current_uses + 1
        WHERE code = %s
    """, (code,))
    
    conn.commit()
    cursor.close()
    conn.close()

def get_user_special_discount(email, plan_name):

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT discount_percent
        FROM user_special_discounts
        WHERE lower(email)=lower(%s)
          AND plan_name=%s
          AND is_active=TRUE
          AND (
                end_date IS NULL
                OR end_date > CURRENT_TIMESTAMP
          )
        LIMIT 1
    """, (email, plan_name))

    row = cur.fetchone()

    cur.close()
    conn.close()

    return int(row["discount_percent"]) if row else 0

def register_user(name, email, phone, password_hash, token):
    conn = get_db_connection()
    cursor = conn.cursor()

    email = email.strip().lower()

    # 1️⃣ Create user with 10 FREE credits
    cursor.execute("""
        INSERT INTO users
            (name, email, phone, auth_provider, password_hash,
             verification_token, credit_cycle_start, credits)
        VALUES
            (%s, %s, %s, 'email', %s,
             %s, CURRENT_TIMESTAMP, 10)
        RETURNING email
    """, (name, email, phone, password_hash, token))

    user_email = cursor.fetchone()[0]

    # 2️⃣ Create FREE subscription for 30 days (ONLY ONCE)
    cursor.execute("""
        INSERT INTO subscriptions
            (user_email, plan, status, start_date, end_date)
        SELECT
            %s, 'Free', 'active', CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP + INTERVAL '30 days'
        WHERE NOT EXISTS (
            SELECT 1 FROM subscriptions WHERE user_email = %s
        )
    """, (user_email, user_email))

    conn.commit()
    cursor.close()
    conn.close()

    return user_email


def verify_user_email(token):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE users SET is_verified = TRUE, verification_token = NULL 
        WHERE verification_token = %s
        RETURNING email
    """, (token,))
    
    result = cursor.fetchone()
    conn.commit()
    cursor.close()
    conn.close()
    return result[0] if result else None

def record_user_coupon_usage(user_email, coupon_code):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO user_coupon_usage (user_email, coupon_code)
        VALUES (%s, %s)
    """, (user_email, coupon_code))
    
    conn.commit()
    cursor.close()
    conn.close()



def payment_exists(stripe_payment_id: str) -> bool:
    """Return True if this Stripe session/payment was already recorded."""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM payments WHERE stripe_payment_id=%s LIMIT 1", (stripe_payment_id,))
    ok = cur.fetchone() is not None
    cur.close()
    conn.close()
    return ok

def set_email_otp(email: str, otp: str, ttl_minutes: int = 10) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    # Safe interval cast avoids SQL formatting issues
    cur.execute("""
        UPDATE users
           SET verification_token=%s,
               otp_expires_at = CURRENT_TIMESTAMP + (%s || ' minutes')::interval
         WHERE email=%s
    """, (otp, str(ttl_minutes), email))
    ok = cur.rowcount > 0
    conn.commit(); cur.close(); conn.close()
    return ok

def verify_email_otp(email: str, otp: str) -> bool:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE users
        SET is_verified = TRUE,
            verification_token = NULL,
            otp_expires_at = NULL
        WHERE email=%s
          AND verification_token=%s
          AND otp_expires_at > CURRENT_TIMESTAMP
        RETURNING email
    """, (email, otp))
    ok = cur.fetchone() is not None
    conn.commit(); cur.close(); conn.close()
    return ok

def record_credit_usage(user_email: str, feature: str, credits: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO credit_usage (user_email, feature, credits)
        VALUES (%s, %s, %s)
    """, (user_email, feature, credits))
    conn.commit()
    cur.close(); conn.close()

def reset_credits_if_expired(email: str) -> bool:
    """
    Initialize a new user's credit cycle without wiping free credits,
    and reset to 0 only when a 1-month cycle has actually expired.
    Returns True if any change was applied.
    """
    if email and ("tester@cvolvepro.com" in email.lower() or "test" in email.lower()):
        return False

    conn = get_db_connection()
    cur = conn.cursor()

    # Case 1: First-time init → set cycle start, keep existing credits (defaults to 5)
    cur.execute("""
        UPDATE users
           SET credit_cycle_start = CURRENT_TIMESTAMP,
               credits = COALESCE(credits, 5)
         WHERE email = %s
           AND credit_cycle_start IS NULL
        RETURNING 1
    """, (email,))
    did = cur.fetchone() is not None

    if not did:
        # Case 2: Cycle expired → zero credits and start new cycle
        cur.execute("""
            UPDATE users
               SET credits = 0,
                   credit_cycle_start = CURRENT_TIMESTAMP
             WHERE email = %s
               AND credit_cycle_start IS NOT NULL
               AND credit_cycle_start + INTERVAL '1 month' <= CURRENT_TIMESTAMP
            RETURNING 1
        """, (email,))
        did = cur.fetchone() is not None

    conn.commit(); cur.close(); conn.close()
    return did

# =========================================================
# ===================== JOBSQA HELPERS ====================
# =========================================================

from werkzeug.security import generate_password_hash, check_password_hash


from psycopg2.extras import RealDictCursor

def jobsqa_get_user_by_email(email):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    try:
        cur.execute(
            "SELECT * FROM jobsqa_users WHERE email = %s",
            (email,)
        )
        return cur.fetchone()   # now a dict
    finally:
        cur.close()
        conn.close()



def jobsqa_create_user(email, password_hash):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # 1️⃣ Create user and get ID
        cur.execute("""
            INSERT INTO jobsqa_users (email, password_hash, is_verified)
            VALUES (%s, %s, FALSE)
            RETURNING id
        """, (email.strip().lower(), password_hash))

        user_id = cur.fetchone()[0]

        # 2️⃣ Give 8 free credits (ONLY ONCE)
        cur.execute("""
            INSERT INTO jobsqa_credits (user_id, credits)
            VALUES (%s, %s)
            ON CONFLICT (user_id) DO NOTHING
        """, (user_id, 8))

        conn.commit()
    finally:
        cur.close()
        conn.close()





from psycopg2.extras import RealDictCursor
from werkzeug.security import check_password_hash

def jobsqa_authenticate(email: str, password: str):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT id, email, password_hash, is_verified
        FROM jobsqa_users
        WHERE email = %s
    """, (email.strip().lower(),))

    user = cur.fetchone()
    cur.close()
    conn.close()

    if not user:
        return None

    if not user["is_verified"]:
        # IMPORTANT: distinct signal
        raise ValueError("EMAIL_NOT_VERIFIED")

    if not check_password_hash(user["password_hash"], password):
        return None

    return user




def jobsqa_get_credits(user_id: int) -> int:
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT credits, expires_at
        FROM jobsqa_credits
        WHERE user_id = %s
    """, (user_id,))
    row = cur.fetchone()

    if not row:
        return 0

    if row["expires_at"] and row["expires_at"] < datetime.utcnow():
        cur.execute("""
            UPDATE jobsqa_credits
            SET credits = 0
            WHERE user_id = %s
        """, (user_id,))
        conn.commit()
        return 0

    return int(row["credits"])




def jobsqa_update_credits(user_id: int, delta: int, action: str):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE jobsqa_credits
            SET credits = credits + %s, updated_at = NOW()
            WHERE user_id = %s
        """, (delta, user_id))

        cur.execute("""
            INSERT INTO jobsqa_credit_logs (user_id, action, credits_change)
            VALUES (%s, %s, %s)
        """, (user_id, action, delta))

        conn.commit()
    finally:
        cur.close()
        conn.close()



def jobsqa_save_interview(user_id: int, resume_filename: str, jd: str, qa: str):
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO jobsqa_interview_history
            (user_id, resume_filename, job_description, interview_qa)
            VALUES (%s, %s, %s, %s)
        """, (user_id, resume_filename, jd, qa))
        conn.commit()
    finally:
        cur.close()
        conn.close()

def jobsqa_set_email_otp(email, otp):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            UPDATE jobsqa_users
            SET email_otp = %s,
                otp_created_at = NOW()
            WHERE email = %s
        """, (otp, email))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def jobsqa_verify_email_otp(email, otp):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT email_otp
            FROM jobsqa_users
            WHERE email = %s
              AND otp_created_at > NOW() - INTERVAL '10 minutes'
        """, (email,))
        row = cur.fetchone()

        if not row or row[0] != otp:
            return False

        cur.execute("""
            UPDATE jobsqa_users
            SET is_verified = TRUE,
                email_otp = NULL
            WHERE email = %s
        """, (email,))
        conn.commit()
        return True
    finally:
        cur.close()
        conn.close()


# =========================================================
# BUSINESS CREDIT HELPERS
# =========================================================

def get_business_credits(email):

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:

        cur.execute("""
            SELECT credits
            FROM business_users
            WHERE email=%s
        """, (email.lower(),))

        row = cur.fetchone()

        if row:
            return row["credits"]

        return 0

    finally:
        cur.close()
        conn.close()


def update_business_credits(email, credits):

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        cur.execute("""
            UPDATE business_users
            SET credits=%s
            WHERE email=%s
        """, (
            credits,
            email.lower()
        ))

        conn.commit()

    finally:
        cur.close()
        conn.close()


def activate_business_plan(
    email,
    plan_name,
    credits,
    expiry
):

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        cur.execute("""
            UPDATE business_users
            SET
                current_plan=%s,
                credits=credits + %s,
                plan_expiry=%s
            WHERE email=%s
        """, (
            plan_name,
            credits,
            expiry,
            email.lower()
        ))

        conn.commit()

    finally:
        cur.close()
        conn.close()


# =========================================================
# BUSINESS PAYMENT HELPERS
# =========================================================

def save_business_payment(
    user_email,
    amount,
    payment_type,
    stripe_session_id,
    credits_purchased=0
):

    conn = get_db_connection()
    cur = conn.cursor()

    try:

        cur.execute("""
            INSERT INTO business_payments (
                user_email,
                amount,
                payment_type,
                stripe_session_id,
                credits_purchased
            )
            VALUES (%s, %s, %s, %s, %s)
        """, (
            user_email,
            amount,
            payment_type,
            stripe_session_id,
            credits_purchased
        ))

        conn.commit()

    finally:
        cur.close()
        conn.close()


def get_business_plan_info(email):

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:

        cur.execute("""
            SELECT
                current_plan,
                plan_expiry,
                credits
            FROM business_users
            WHERE email=%s
        """, (email.lower(),))

        return cur.fetchone()

    finally:
        cur.close()
        conn.close()


