#!/usr/bin/env python3
"""
Idempotent DB migration for the coupon system.

Creates the `coupons` and `coupon_redemptions` tables (if they don't exist)
and verifies their presence. Safe to re-run.

Usage:
    source .venv/bin/activate
    python scripts/migrate_coupons.py
"""
import os
import sys
from pathlib import Path

# Make repo root importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from database import init_db, get_db_connection, release_db_connection


EXPECTED_TABLES = {"coupons", "coupon_redemptions"}


def _table_exists(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
             WHERE table_schema = 'public' AND table_name = %s
             LIMIT 1
            """,
            (name,),
        )
        return cur.fetchone() is not None


def main() -> int:
    print("[1/2] Running init_db() (idempotent)...")
    try:
        init_db()
    except Exception as e:
        print(f"  ✗ init_db failed: {e}")
        return 1
    print("  ✓ init_db() succeeded")

    print("[2/2] Verifying expected tables exist...")
    conn = get_db_connection()
    try:
        missing = [t for t in EXPECTED_TABLES if not _table_exists(conn, t)]
    finally:
        release_db_connection(conn)

    if missing:
        print(f"  ✗ Missing tables after migration: {', '.join(missing)}")
        return 1
    for t in sorted(EXPECTED_TABLES):
        print(f"  ✓ {t}")
    print("\nMigration complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
