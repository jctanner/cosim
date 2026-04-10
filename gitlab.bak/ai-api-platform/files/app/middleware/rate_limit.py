from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import redis.asyncio as redis
from app.config import settings
from app.errors import RateLimitExceededError
import time
from datetime import datetime, timezone

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.redis = redis.from_url(settings.redis_url)
        self.limit = settings.rate_limit_requests
        self.window = settings.rate_limit_window
    
    async def dispatch(self, request: Request, call_next):
        # Extract API key from header
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            response = await call_next(request)
            return response
        
        # Check rate limit using Redis
        key = f"rate_limit:{api_key}"
        
        try:
            current = await self.redis.get(key)
            
            if current is None:
                # First request in this window
                await self.redis.setex(key, self.window, 1)
                remaining = self.limit - 1
            elif int(current) >= self.limit:
                # Rate limit exceeded - calculate reset time
                ttl = await self.redis.ttl(key)
                reset_unix = int(time.time()) + ttl
                reset_at = datetime.fromtimestamp(reset_unix, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                
                # Raise spec-compliant error
                raise RateLimitExceededError(
                    rate_limit_tpm=self.limit * (60 // self.window),  # Convert to per-minute rate
                    tokens_remaining=0,
                    reset_at=reset_at,
                    retry_after_seconds=ttl
                )
            else:
                # Increment counter
                await self.redis.incr(key)
                remaining = self.limit - int(current) - 1
        except RateLimitExceededError:
            raise  # Re-raise to be handled by FastAPI
        except Exception as e:
            # Redis failure - fail open (allow request) but log error
            import logging
            logging.error(f"Rate limit check failed: {e}")
            response = await call_next(request)
            return response
        
        # Process request
        response = await call_next(request)
        
        # Add rate limit headers to successful responses
        response.headers['X-RateLimit-Limit'] = str(self.limit)
        response.headers['X-RateLimit-Remaining'] = str(max(0, remaining))
        
        return response
