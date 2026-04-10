from prometheus_client import Counter, Histogram, Gauge

# Request metrics per architecture spec
api_requests_total = Counter(
    'api_requests_total',
    'Total API requests',
    ['customer_id', 'model', 'status']
)

api_request_duration_seconds = Histogram(
    'api_request_duration_seconds',
    'API request latency',
    ['customer_id', 'model']
)

api_request_cost_usd = Counter(
    'api_request_cost_usd',
    'Total API cost in USD',
    ['customer_id', 'model']
)

api_tokens_total = Counter(
    'api_tokens_total',
    'Total tokens processed',
    ['customer_id', 'model', 'direction']  # direction: input or output
)

api_requests_throttled_total = Counter(
    'api_requests_throttled_total',
    'Total throttled requests',
    ['customer_id', 'reason']  # reason: budget_exceeded, rate_limit_exceeded
)

# Operational metrics
audit_write_failures = Counter(
    'audit_write_failures_total',
    'Failed audit log writes to Postgres (data preserved in structured logs)'
)

redis_budget_update_failures = Counter(
    'redis_budget_update_failures_total',
    'Failed Redis budget counter updates'
)

# Data integrity metrics for TK-CAF42C
llm_requests_total = Counter(
    'llm_requests_total',
    'Total LLM requests received (for audit reconciliation)',
    ['customer_id']
)

postgres_audit_records_inserted = Counter(
    'postgres_audit_records_inserted',
    'Successfully inserted audit records in PostgreSQL',
    ['customer_id']
)

# Database connection pool metrics
db_connection_pool_size = Gauge(
    'db_connection_pool_size',
    'Total database connection pool size'
)

db_connection_pool_available = Gauge(
    'db_connection_pool_available',
    'Available database connections'
)

def record_request_metrics(customer_id: str, model: str, status: str, 
                          latency_ms: float, cost_usd: float,
                          tokens_input: int, tokens_output: int,
                          throttled: bool, throttle_reason: str = None):
    """Record custom metrics for API requests"""
    
    api_requests_total.labels(
        customer_id=customer_id,
        model=model,
        status=status
    ).inc()
    
    api_request_duration_seconds.labels(
        customer_id=customer_id,
        model=model
    ).observe(latency_ms / 1000.0)
    
    api_request_cost_usd.labels(
        customer_id=customer_id,
        model=model
    ).inc(cost_usd)
    
    api_tokens_total.labels(
        customer_id=customer_id,
        model=model,
        direction='input'
    ).inc(tokens_input)
    
    api_tokens_total.labels(
        customer_id=customer_id,
        model=model,
        direction='output'
    ).inc(tokens_output)
    
    if throttled and throttle_reason:
        api_requests_throttled_total.labels(
            customer_id=customer_id,
            reason=throttle_reason
        ).inc()
