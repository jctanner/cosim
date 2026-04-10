from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse
from typing import Optional
import time

class APIError(HTTPException):
    """Base class for API errors with structured response format"""
    
    def __init__(self, status_code: int, error_code: str, message: str, headers: Optional[dict] = None, **kwargs):
        detail = {
            "error": error_code,
            "message": message,
            "details": kwargs
        }
        super().__init__(status_code=status_code, detail=detail, headers=headers)

class BudgetExceededError(APIError):
    def __init__(self, budget_limit_usd: float, budget_spent_usd: float, reset_date: str):
        message = (
            f"Monthly budget of ${budget_limit_usd:.2f} exceeded. "
            f"Current usage: ${budget_spent_usd:.2f}. "
            f"Increase your limit in the dashboard or wait until {reset_date}."
        )
        super().__init__(
            status_code=429,
            error_code="budget_exceeded",
            message=message,
            budget_period="monthly",
            budget_limit_usd=budget_limit_usd,
            budget_spent_usd=budget_spent_usd,
            budget_remaining_usd=budget_limit_usd - budget_spent_usd,
            reset_date=reset_date,
            dashboard_url="https://dashboard.example.com/billing"
        )

class RateLimitExceededError(APIError):
    def __init__(self, rate_limit_tpm: int, tokens_remaining: int, reset_at: str, retry_after_seconds: int):
        message = (
            f"Rate limit of {rate_limit_tpm:,} requests/minute exceeded. "
            f"Quota resets at {reset_at}. "
            f"Retry after {retry_after_seconds} seconds."
        )
        
        # Calculate reset_unix from reset_at ISO8601 string
        from datetime import datetime
        reset_unix = int(datetime.fromisoformat(reset_at.replace('Z', '+00:00')).timestamp())
        
        # Build headers per spec
        headers = {
            'Retry-After': str(retry_after_seconds),
            'X-RateLimit-Limit': str(rate_limit_tpm),
            'X-RateLimit-Remaining': str(tokens_remaining),
            'X-RateLimit-Reset': str(reset_unix)
        }
        
        super().__init__(
            status_code=429,
            error_code="rate_limit_exceeded",
            message=message,
            headers=headers,
            rate_limit_type="requests_per_minute",
            limit=rate_limit_tpm,
            remaining=tokens_remaining,
            reset_at=reset_at,
            reset_unix=reset_unix,
            retry_after_seconds=retry_after_seconds,
            algorithm="token_bucket",
            documentation_url="https://docs.example.com/rate-limits"
        )

class AuthenticationError(APIError):
    def __init__(self, message: str = "Invalid or missing API key"):
        super().__init__(
            status_code=401,
            error_code="authentication_failed",
            message=message
        )

class InvalidRequestError(APIError):
    def __init__(self, message: str, field: Optional[str] = None):
        kwargs = {}
        if field:
            kwargs['field'] = field
        super().__init__(
            status_code=400,
            error_code="invalid_request",
            message=message,
            **kwargs
        )
