from fastapi import APIRouter, Request, Header
from typing import Optional, Dict, Any
from app.errors import BudgetExceededError, RateLimitExceededError, AuthenticationError
from app.middleware.metrics import record_request_metrics
from app.config import settings
import time
from datetime import datetime
import logging
import openai
import tiktoken

logger = logging.getLogger(__name__)

router = APIRouter(tags=["proxy"])

# Pricing data per architecture spec (should eventually come from database)
PRICING = {
    "gpt-4": {"input": 0.03, "output": 0.06},
    "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
    "gpt-3.5-turbo": {"input": 0.0015, "output": 0.002},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
}
PRICING_VERSION = 1

def count_tokens(text: str, model: str) -> int:
    """Count tokens using tiktoken for OpenAI models"""
    try:
        # Map model names to tiktoken encodings
        if "gpt-4" in model:
            encoding = tiktoken.encoding_for_model("gpt-4")
        elif "gpt-3.5" in model:
            encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        else:
            # Fallback to cl100k_base for unknown models
            encoding = tiktoken.get_encoding("cl100k_base")
        
        return len(encoding.encode(text))
    except Exception as e:
        logger.error(f"Token counting failed: {e}")
        # Conservative estimate: ~4 chars per token
        return len(text) // 4

def count_message_tokens(messages: list, model: str) -> int:
    """Count tokens in a messages array"""
    total = 0
    for msg in messages:
        if isinstance(msg, dict) and "content" in msg:
            total += count_tokens(str(msg["content"]), model)
        # Add overhead for message formatting (role, etc.)
        total += 4
    return total

def calculate_cost(tokens_input: int, tokens_output: int, model: str) -> tuple:
    """Calculate cost in USD for input/output tokens"""
    pricing = PRICING.get(model, {"input": 0.01, "output": 0.03})  # Default pricing
    
    cost_input = (tokens_input / 1000.0) * pricing["input"]
    cost_output = (tokens_output / 1000.0) * pricing["output"]
    
    return round(cost_input, 4), round(cost_output, 4)

@router.post("/llm/proxy")
async def proxy_llm_request(
    request: Request,
    body: Dict[Any, Any],
    x_api_key: Optional[str] = Header(None),
    x_target_provider: Optional[str] = Header(None)
):
    """
    Proxy endpoint for LLM requests with authentication, rate limiting, and budget enforcement.
    
    Full implementation:
    1. API key validation - DONE via AuthenticationMiddleware
    2. Rate limit check - DONE via RateLimitMiddleware
    3. Budget check - DONE via BudgetEnforcementMiddleware (Casey)
    4. Provider proxy (OpenAI SDK) - IMPLEMENTED HERE
    5. Token counting and cost calculation - IMPLEMENTED HERE
    6. Audit logging - DONE via AuditLoggingMiddleware (Casey)
    7. Metrics recording - IMPLEMENTED HERE
    """
    
    start_time = time.time()
    
    # Extract customer context from auth middleware
    customer_id = getattr(request.state, 'customer_id', None)
    if not customer_id:
        raise AuthenticationError("Customer ID not found. Authentication failed.")
    
    # Extract request parameters
    model = body.get("model", "gpt-3.5-turbo")
    messages = body.get("messages", [])
    temperature = body.get("temperature", 1.0)
    max_tokens = body.get("max_tokens")
    
    # Count input tokens
    tokens_input = count_message_tokens(messages, model)
    
    try:
        # Call OpenAI API
        client = openai.OpenAI(api_key=settings.openai_api_key)
        
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        # Extract response data
        response_content = response.choices[0].message.content
        tokens_output = response.usage.completion_tokens if response.usage else count_tokens(response_content, model)
        
        # Calculate costs
        cost_input_usd, cost_output_usd = calculate_cost(tokens_input, tokens_output, model)
        
        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Prepare audit data for Casey's middleware
        request.state.llm_request_data = {
            "customer_id": customer_id,
            "timestamp": datetime.utcnow(),
            "model": model,
            "endpoint": "/v1/llm/proxy",
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "cost_input_usd": cost_input_usd,
            "cost_output_usd": cost_output_usd,
            "pricing_version": PRICING_VERSION,
            "response_status": 200,
            "response_latency_ms": latency_ms,
            "throttled": getattr(request.state, 'throttled', False),
            "throttle_reason": getattr(request.state, 'throttle_reason', None),
            "rate_limit_remaining": getattr(request.state, 'rate_limit_remaining', None),
            "rate_limit_reset_at": getattr(request.state, 'rate_limit_reset_at', None),
            "budget_spent_usd": getattr(request.state, 'budget_spent_usd', None),
            "budget_limit_usd": getattr(request.state, 'budget_limit_usd', None),
        }
        
        # Record metrics
        record_request_metrics(
            customer_id=customer_id,
            model=model,
            status="success",
            latency_ms=latency_ms,
            cost_usd=cost_input_usd + cost_output_usd,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            throttled=getattr(request.state, 'throttled', False)
        )
        
        # Return OpenAI-compatible response
        return {
            "id": response.id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_content
                    },
                    "finish_reason": response.choices[0].finish_reason
                }
            ],
            "usage": {
                "prompt_tokens": tokens_input,
                "completion_tokens": tokens_output,
                "total_tokens": tokens_input + tokens_output
            }
        }
        
    except openai.APIError as e:
        logger.error(f"OpenAI API error: {e}")
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Record failed request for audit
        request.state.llm_request_data = {
            "customer_id": customer_id,
            "timestamp": datetime.utcnow(),
            "model": model,
            "endpoint": "/v1/llm/proxy",
            "tokens_input": tokens_input,
            "tokens_output": 0,
            "cost_input_usd": 0,
            "cost_output_usd": 0,
            "pricing_version": PRICING_VERSION,
            "response_status": 502,
            "response_latency_ms": latency_ms,
            "throttled": getattr(request.state, 'throttled', False),
            "throttle_reason": getattr(request.state, 'throttle_reason', None),
            "rate_limit_remaining": getattr(request.state, 'rate_limit_remaining', None),
            "rate_limit_reset_at": getattr(request.state, 'rate_limit_reset_at', None),
            "budget_spent_usd": getattr(request.state, 'budget_spent_usd', None),
            "budget_limit_usd": getattr(request.state, 'budget_limit_usd', None),
        }
        
        raise
    
    except Exception as e:
        logger.error(f"Proxy error: {e}")
        latency_ms = int((time.time() - start_time) * 1000)
        
        # Record failed request for audit
        request.state.llm_request_data = {
            "customer_id": customer_id,
            "timestamp": datetime.utcnow(),
            "model": model,
            "endpoint": "/v1/llm/proxy",
            "tokens_input": tokens_input,
            "tokens_output": 0,
            "cost_input_usd": 0,
            "cost_output_usd": 0,
            "pricing_version": PRICING_VERSION,
            "response_status": 500,
            "response_latency_ms": latency_ms,
            "throttled": getattr(request.state, 'throttled', False),
            "throttle_reason": getattr(request.state, 'throttle_reason', None),
            "rate_limit_remaining": getattr(request.state, 'rate_limit_remaining', None),
            "rate_limit_reset_at": getattr(request.state, 'rate_limit_reset_at', None),
            "budget_spent_usd": getattr(request.state, 'budget_spent_usd', None),
            "budget_limit_usd": getattr(request.state, 'budget_limit_usd', None),
        }
        
        raise

@router.get("/pricing/current")
async def get_current_pricing():
    """Return current pricing per architecture spec"""
    return {
        "version": PRICING_VERSION,
        "effective_date": "2026-03-01T00:00:00Z",
        "models": {
            model: {
                "input_cost_per_1k_tokens": prices["input"],
                "output_cost_per_1k_tokens": prices["output"]
            }
            for model, prices in PRICING.items()
        },
        "changelog_url": "https://docs.example.com/pricing-history"
    }

@router.get("/usage/summary")
async def get_usage_summary(
    period: str = "monthly",
    x_api_key: Optional[str] = Header(None)
):
    """Return usage summary per architecture spec"""
    # TODO: Implement actual lookup from database
    return {
        "period": period,
        "period_start": "2026-03-01T00:00:00Z",
        "period_end": "2026-04-01T00:00:00Z",
        "budget_limit_usd": 500.00,
        "budget_spent_usd": 0.00,
        "budget_remaining_usd": 500.00,
        "request_count": 0,
        "total_tokens": 0,
        "top_models": []
    }
