from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import logging
import time
import json
import uuid

logger = logging.getLogger(__name__)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Generate request_id for tracing
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        start_time = time.time()
        
        # Extract customer_id from API key (set by auth middleware)
        customer_id = getattr(request.state, 'customer_id', None)
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Structured log format per architecture spec
            log_data = {
                "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                "level": "info",
                "event": "request_completed",
                "request_id": request_id,
                "customer_id": customer_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": round(duration_ms, 2),
                "client_ip": request.client.host if request.client else None,
            }
            
            # Add model and tokens if available (set by proxy handler)
            if hasattr(request.state, 'model'):
                log_data['model'] = request.state.model
            if hasattr(request.state, 'tokens_input'):
                log_data['tokens_input'] = request.state.tokens_input
            if hasattr(request.state, 'tokens_output'):
                log_data['tokens_output'] = request.state.tokens_output
            if hasattr(request.state, 'cost_usd'):
                log_data['cost_usd'] = request.state.cost_usd
            if hasattr(request.state, 'throttled'):
                log_data['throttled'] = request.state.throttled
            
            logger.info(json.dumps(log_data))
            
            # Add request_id to response headers for debugging
            response.headers['X-Request-ID'] = request_id
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            
            log_data = {
                "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                "level": "error",
                "event": "request_failed",
                "request_id": request_id,
                "customer_id": customer_id,
                "method": request.method,
                "path": request.url.path,
                "latency_ms": round(duration_ms, 2),
                "error": str(e),
            }
            
            logger.error(json.dumps(log_data))
            raise
