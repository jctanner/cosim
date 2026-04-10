from fastapi import FastAPI
from prometheus_client import make_asgi_app
from app.middleware.auth import AuthenticationMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.logging import LoggingMiddleware
from app.middleware.budget import BudgetEnforcementMiddleware
from app.middleware.audit import AuditMiddleware
from app.routes import health, api, proxy
from app.config import settings

# Import metrics to ensure they're registered
import app.middleware.metrics

app = FastAPI(title="AI API Platform", version="0.1.0")

# Add middleware (order matters - applied in reverse)
# Execution order: Logging → Audit → Budget → RateLimit → Auth
app.add_middleware(LoggingMiddleware)  # Wraps everything
app.add_middleware(AuditMiddleware)  # Audit after response
app.add_middleware(BudgetEnforcementMiddleware)  # Check budget before proxy
app.add_middleware(RateLimitMiddleware)  # Rate limit after budget
app.add_middleware(AuthenticationMiddleware)  # Auth first

# Include routers
app.include_router(health.router)
app.include_router(api.router, prefix="/v1")  # Legacy endpoints
app.include_router(proxy.router, prefix="/v1")  # Proxy endpoints per spec

# Metrics endpoint - exposes custom metrics defined in app.middleware.metrics
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
