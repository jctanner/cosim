-- LLM requests audit trail per architecture spec
-- Replaces api_requests table from earlier design

CREATE TABLE llm_requests (
    request_id UUID PRIMARY KEY,
    customer_id VARCHAR(255) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Request details
    model VARCHAR(100) NOT NULL,
    endpoint VARCHAR(255) NOT NULL,
    
    -- Token and cost tracking
    tokens_input INTEGER NOT NULL,
    tokens_output INTEGER NOT NULL,
    tokens_total INTEGER GENERATED ALWAYS AS (tokens_input + tokens_output) STORED,
    
    -- Cost calculation
    cost_input_usd DECIMAL(10,4) NOT NULL,
    cost_output_usd DECIMAL(10,4) NOT NULL,
    cost_total_usd DECIMAL(10,4) GENERATED ALWAYS AS (cost_input_usd + cost_output_usd) STORED,
    pricing_version INTEGER NOT NULL,
    
    -- Rate limit and budget state snapshot
    rate_limit_remaining INTEGER,
    rate_limit_reset_at TIMESTAMPTZ,
    budget_spent_usd DECIMAL(10,2),
    budget_limit_usd DECIMAL(10,2),
    
    -- Response metadata
    response_status INTEGER,
    response_latency_ms INTEGER,
    throttled BOOLEAN DEFAULT FALSE,
    throttle_reason VARCHAR(255)
);

CREATE INDEX idx_llm_customer_timestamp ON llm_requests(customer_id, timestamp DESC);
CREATE INDEX idx_llm_customer_cost ON llm_requests(customer_id, cost_total_usd DESC);
CREATE INDEX idx_llm_timestamp ON llm_requests(timestamp DESC);
CREATE INDEX idx_llm_throttled ON llm_requests(throttled, customer_id) WHERE throttled = TRUE;

-- Pricing versions table
CREATE TABLE pricing_versions (
    version INTEGER PRIMARY KEY,
    effective_date TIMESTAMPTZ NOT NULL,
    pricing_data JSONB NOT NULL,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Customers table
CREATE TABLE customers (
    customer_id VARCHAR(255) PRIMARY KEY,
    api_key_hash VARCHAR(255) NOT NULL UNIQUE,
    budget_monthly_usd DECIMAL(10,2),
    budget_daily_usd DECIMAL(10,2),
    rate_limit_tpm INTEGER DEFAULT 100000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
