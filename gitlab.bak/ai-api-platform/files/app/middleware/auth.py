from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from app.database import SessionLocal
from app.models.customer import Customer
from app.errors import AuthenticationError
import bcrypt

class AuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Authentication middleware - validates API keys and loads customer config

    Per TK-9E0440:
    - Validates X-API-Key header against customers table
    - Uses bcrypt for secure hash comparison
    - Loads customer configuration into request.state.customer
    - Raises AuthenticationError (401) for invalid/missing keys
    """

    async def dispatch(self, request: Request, call_next):
        # Skip auth for health/metrics endpoints
        if request.url.path in ['/health', '/metrics', '/']:
            response = await call_next(request)
            return response

        # Extract API key from header
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            raise AuthenticationError("Missing API key. Provide X-API-Key header.")

        # Validate API key against database
        db = SessionLocal()
        try:
            # Try to find customer by matching API key hash
            customers = db.query(Customer).all()
            customer = None

            for cust in customers:
                # Compare provided key against stored bcrypt hash
                if bcrypt.checkpw(api_key.encode('utf-8'), cust.api_key_hash.encode('utf-8')):
                    customer = cust
                    break

            if not customer:
                raise AuthenticationError("Invalid API key. Check your credentials.")

            # Load customer config into request state for downstream middleware
            request.state.customer = customer
            request.state.customer_id = customer.customer_id
            request.state.budget_monthly_usd = float(customer.budget_monthly_usd) if customer.budget_monthly_usd else None
            request.state.budget_daily_usd = float(customer.budget_daily_usd) if customer.budget_daily_usd else None
            request.state.rate_limit_tpm = customer.rate_limit_tpm

        except AuthenticationError:
            raise  # Re-raise authentication errors
        except Exception as e:
            # Database or other errors - fail closed for security
            import logging
            logging.error(f"Authentication check failed: {e}")
            raise AuthenticationError("Authentication service unavailable")
        finally:
            db.close()

        # Process request
        response = await call_next(request)
        return response
