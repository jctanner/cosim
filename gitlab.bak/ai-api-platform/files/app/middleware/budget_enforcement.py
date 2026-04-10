from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.errors import BudgetExceededError
import redis
import logging
from datetime import datetime
from app.config import settings

logger = logging.getLogger(__name__)

class BudgetEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Budget enforcement middleware - prevents requests when customer exceeds budget limits
    
    Per TK-1DD2DC:
    - Checks Redis budget state BEFORE allowing proxy requests
    - Enforces monthly and daily budget limits from customer config
    - Raises BudgetExceededError (429) when limits exceeded
    - Updates budget counters with actual spend AFTER request completes
    """

    def __init__(self, app):
        super().__init__(app)
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)

    async def dispatch(self, request: Request, call_next):
        # Skip budget checks for non-proxy endpoints
        if not request.url.path.startswith('/v1/llm/'):
            response = await call_next(request)
            return response

        # Get customer budget config from request state (set by auth middleware)
        customer_id = getattr(request.state, 'customer_id', None)
        budget_monthly_usd = getattr(request.state, 'budget_monthly_usd', None)
        budget_daily_usd = getattr(request.state, 'budget_daily_usd', None)

        if not customer_id:
            # Auth middleware should have set this - fail safe
            logger.error("Budget enforcement called without customer_id in request state")
            response = await call_next(request)
            return response

        # Check budget limits from Redis
        current_month = datetime.utcnow().strftime('%Y-%m')
        current_date = datetime.utcnow().strftime('%Y-%m-%d')
        
        monthly_key = f"budget:monthly:{customer_id}:{current_month}"
        daily_key = f"budget:daily:{customer_id}:{current_date}"

        try:
            # Get current spend from Redis
            monthly_spend = float(self.redis_client.get(monthly_key) or 0.0)
            daily_spend = float(self.redis_client.get(daily_key) or 0.0)

            # Check monthly budget
            if budget_monthly_usd and monthly_spend >= budget_monthly_usd:
                raise BudgetExceededError(
                    f"Monthly budget limit of ${budget_monthly_usd:.2f} exceeded. "
                    f"Current spend: ${monthly_spend:.2f}. Contact support to increase limits."
                )

            # Check daily budget
            if budget_daily_usd and daily_spend >= budget_daily_usd:
                raise BudgetExceededError(
                    f"Daily budget limit of ${budget_daily_usd:.2f} exceeded. "
                    f"Current spend: ${daily_spend:.2f}. Limit resets at midnight UTC."
                )

        except BudgetExceededError:
            raise  # Re-raise budget errors
        except Exception as e:
            logger.error(f"Budget check failed for customer {customer_id}: {e}")
            # Fail open for Redis errors - don't block customer requests
            # Log the error and let request proceed

        # Budget check passed - process request
        response = await call_next(request)

        # Update budget counters with actual spend (populated by proxy endpoint)
        try:
            llm_request_data = getattr(request.state, 'llm_request_data', None)
            if llm_request_data:
                # Calculate total cost from input + output costs
                cost_input = float(llm_request_data.get('cost_input_usd', 0))
                cost_output = float(llm_request_data.get('cost_output_usd', 0))
                total_cost = cost_input + cost_output
                
                if total_cost > 0:
                    # Increment monthly and daily spend
                    self.redis_client.incrbyfloat(monthly_key, total_cost)
                    self.redis_client.incrbyfloat(daily_key, total_cost)
                    
                    # Set expiration on keys (monthly: 60 days, daily: 7 days)
                    self.redis_client.expire(monthly_key, 60 * 24 * 60 * 60)
                    self.redis_client.expire(daily_key, 7 * 24 * 60 * 60)
                    
                    logger.info(f"Updated budget for customer {customer_id}: +${total_cost:.4f}")
        except Exception as e:
            logger.error(f"Failed to update budget counters for customer {customer_id}: {e}")
            # Don't fail the request if budget update fails - audit trail has the data

        return response
