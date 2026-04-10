#!/usr/bin/env python3
"""
Generate bcrypt hash for API keys

Usage:
    python scripts/generate_api_key_hash.py

This script generates a secure random API key and its bcrypt hash.
The API key should be provided to the customer (store securely!).
The hash should be stored in the customers table.
"""

import bcrypt
import secrets
import string

def generate_api_key(length=32):
    """Generate a cryptographically secure random API key"""
    alphabet = string.ascii_letters + string.digits
    api_key = ''.join(secrets.choice(alphabet) for _ in range(length))
    return f"sk_{api_key}"  # Prefix with 'sk_' like Stripe/OpenAI

def hash_api_key(api_key: str) -> str:
    """Generate bcrypt hash of API key"""
    return bcrypt.hashpw(api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

if __name__ == "__main__":
    print("=" * 80)
    print("API Key Generator")
    print("=" * 80)
    print()

    # Generate API key
    api_key = generate_api_key()
    api_key_hash = hash_api_key(api_key)

    print("🔑 API Key (give to customer - STORE SECURELY!):")
    print(f"   {api_key}")
    print()
    print("🔒 Bcrypt Hash (store in database):")
    print(f"   {api_key_hash}")
    print()
    print("=" * 80)
    print("⚠️  IMPORTANT: Save the API key NOW!")
    print("   The plain API key cannot be recovered after this.")
    print("   Only the hash will be stored in the database.")
    print("=" * 80)
    print()
    print("SQL to insert customer:")
    print(f"""
INSERT INTO customers (
    customer_id,
    api_key_hash,
    budget_monthly_usd,
    budget_daily_usd,
    rate_limit_tpm
) VALUES (
    'customer_id_here',
    '{api_key_hash}',
    1000.00,
    50.00,
    100000
);
""")
