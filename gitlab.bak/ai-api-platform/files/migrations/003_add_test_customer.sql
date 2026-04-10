-- Add test customer for development/testing
-- API Key: test_api_key_dev_only
-- This is a development-only migration and should NOT be run in production

-- Note: The hash below is bcrypt hash of 'test_api_key_dev_only'
-- Generated with: bcrypt.hashpw(b'test_api_key_dev_only', bcrypt.gensalt())

INSERT INTO customers (
    customer_id,
    api_key_hash,
    budget_monthly_usd,
    budget_daily_usd,
    rate_limit_tpm,
    created_at,
    updated_at
) VALUES (
    'test_customer_dev',
    '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewY0BHwLVQ2qBdJ2',  -- bcrypt hash of 'test_api_key_dev_only'
    1000.00,  -- $1000/month budget
    50.00,    -- $50/day budget
    100000,   -- 100k tokens per minute
    NOW(),
    NOW()
) ON CONFLICT (customer_id) DO NOTHING;

-- Add a pricing version for testing
INSERT INTO pricing_versions (
    version,
    effective_date,
    pricing_data,
    notes,
    created_at,
    updated_at
) VALUES (
    1,
    NOW(),
    '{
        "gpt-4": {
            "input_cost_per_1k_tokens": 0.03,
            "output_cost_per_1k_tokens": 0.06
        },
        "gpt-3.5-turbo": {
            "input_cost_per_1k_tokens": 0.0015,
            "output_cost_per_1k_tokens": 0.002
        },
        "claude-3-opus": {
            "input_cost_per_1k_tokens": 0.015,
            "output_cost_per_1k_tokens": 0.075
        },
        "claude-3-sonnet": {
            "input_cost_per_1k_tokens": 0.003,
            "output_cost_per_1k_tokens": 0.015
        }
    }'::jsonb,
    'Initial pricing version for beta',
    NOW(),
    NOW()
) ON CONFLICT (version) DO NOTHING;
