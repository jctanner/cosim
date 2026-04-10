from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.database import SessionLocal
from app.models.llm_request import LLMRequest
from app.middleware.metrics import (
    audit_write_failures,
    redis_budget_update_failures,
    llm_requests_total,
    postgres_audit_records_inserted
)
import redis
import logging
import json
from app.config import settings

logger = logging.getLogger(__name__)

class AuditMiddleware(BaseHTTPMiddleware):
    """
    Audit logging middleware - writes request data to llm_requests table
    
    Per TK-B8260A:
    - Writes audit trail after proxy endpoint returns
    - Updates Redis budget counters with actual spend
    - Basic error handling for beta: logs failures, doesn't fail customer requests
    - Full graceful degradation (alerting, reconstruction) ships in TK-CAF42C
    """

    async def dispatch(self, request: Request, call_next):
        # Process the request
        response = await call_next(request)
        
        # Only audit proxy requests that have llm_request_data populated
        if not hasattr(request.state, 'llm_request_data'):
            return response

        audit_data = request.state.llm_request_data
        customer_id = audit_data.get('customer_id')

        # Track total LLM requests for data integrity monitoring (TK-CAF42C)
        llm_requests_total.labels(customer_id=customer_id).inc()
        
        # Step 1: Update Redis budget counter FIRST (before Postgres write)
        # This ensures budget tracking stays accurate even if database fails
        try:
            r = redis.from_url(settings.redis_url)
            cost_total = float(audit_data.get('cost_input_usd', 0)) + float(audit_data.get('cost_output_usd', 0))
            
            # Increment monthly and daily budget counters
            r.incrbyfloat(f'budget:{customer_id}:monthly', cost_total)
            r.incrbyfloat(f'budget:{customer_id}:daily', cost_total)
            
        except Exception as e:
            redis_budget_update_failures.inc()
            logger.error(
                f"Redis budget update failed for {customer_id}: {e}",
                extra={'audit_data': json.dumps(audit_data, default=str)}
            )
            # Continue to Postgres write even if Redis fails
        
        # Step 2: Write to Postgres llm_requests table
        # Basic error handling for beta: log failures but don't fail customer request
        db = SessionLocal()
        try:
            llm_request = LLMRequest(
                customer_id=audit_data['customer_id'],
                timestamp=audit_data['timestamp'],
                model=audit_data['model'],
                endpoint=audit_data['endpoint'],
                tokens_input=audit_data['tokens_input'],
                tokens_output=audit_data['tokens_output'],
                cost_input_usd=audit_data['cost_input_usd'],
                cost_output_usd=audit_data['cost_output_usd'],
                pricing_version=audit_data['pricing_version'],
                response_status=audit_data['response_status'],
                response_latency_ms=audit_data['response_latency_ms'],
                throttled=audit_data['throttled'],
                throttle_reason=audit_data.get('throttle_reason'),
                rate_limit_remaining=audit_data.get('rate_limit_remaining'),
                rate_limit_reset_at=audit_data.get('rate_limit_reset_at'),
                budget_spent_usd=audit_data.get('budget_spent_usd'),
                budget_limit_usd=audit_data.get('budget_limit_usd')
            )
            
            db.add(llm_request)
            db.commit()

            # Track successful audit record insertion (TK-CAF42C)
            postgres_audit_records_inserted.labels(customer_id=customer_id).inc()

        except Exception as e:
            # Increment Prometheus metric for alerting (TK-CAF42C)
            audit_write_failures.inc()

            # CRITICAL: Log the complete audit data to structured logs
            # For beta: manual reconciliation from logs is acceptable for 2-3 customers
            # For production (TK-CAF42C): automated alerting and reconstruction
            logger.error(
                f"Audit write to llm_requests failed for {customer_id}: {e}",
                extra={
                    'event': 'audit_write_failure',
                    'customer_id': customer_id,
                    'audit_data': json.dumps(audit_data, default=str),
                    'error': str(e)
                }
            )

            # DO NOT raise - customer already got their response successfully
            # We must not turn a successful proxy call into a 500 error
            
        finally:
            db.close()
        
        return response
