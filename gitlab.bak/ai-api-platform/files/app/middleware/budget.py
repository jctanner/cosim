from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.errors import BudgetExceededError
import redis
from app.config import settings
import logging

logger = logging.getLogger(__name__)

class BudgetEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Budget enforcement middleware - checks Redis budget state before proxying
    
    Per TK-1DD2DC:
    - Checks customer budget limits (monthly/daily) from Redis
    - Raises BudgetExceededError if customer has exceeded limits
    - Sets request.state.throttle_reason when budget exceeded
    - Prevents runaway costs before OpenAI API calls happen
    """

    async def dispatch(self, request: Request, call_next):
        # Only check budget for proxy requests
        if not request.url.path.startswith('/v1/llm/proxy'):
            response = await call_next(request)
            return response
        
        # Get customer budget limits from request state (set by auth middleware)
        customer_id = getattr(request.state, 'customer_id', None)
        budget_monthly_usd = getattr(request.state, 'budget_monthly_usd', None)
        budget_daily_usd = getattr(request.state, 'budget_daily_usd', None)
        
        if not customer_id:
            # No customer_id means auth middleware didn't run - should not happen
            response = await call_next(request)
            return response
        
        # Check budget limits from Redis
        r = redis.from_url(settings.redis_url)
        
        try:
            # Get current spend from Redis
            monthly_spend = float(r.get(f'budget:{customer_id}:monthly') or 0)
            daily_spend = float(r.get(f'budget:{customer_id}:daily') or 0)
            
            # Check monthly budget
            if budget_monthly_usd and monthly_spend >= budget_monthly_usd:
                request.state.throttle_reason = 'budget_exceeded_monthly'
                raise BudgetExceededError(
                    f"Monthly budget limit of ${budget_monthly_usd} exceeded. Current spend: ${monthly_spend:.2f}"
                )
            
            # Check daily budget
            if budget_daily_usd and daily_spend >= budget_daily_usd:
                request.state.throttle_reason = 'budget_exceeded_daily'
                raise BudgetExceededError(
                    f"Daily budget limit of ${budget_daily_usd} exceeded. Current spend: ${daily_spend:.2f}"
                )
            
            # Budget OK - store current spend for audit logging
            request.state.budget_spent_usd = monthly_spend
            request.state.budget_limit_usd = budget_monthly_usd
            
        except BudgetExceededError:
            raise  # Re-raise budget errors
        except Exception as e:
            logger.error(f"Budget check failed for {customer_id}: {e}")
            # Fail open for beta - allow request if Redis is down
            # TODO: For production, consider fail-closed policy
        
        # Process request
        response = await call_next(request)
        return response
