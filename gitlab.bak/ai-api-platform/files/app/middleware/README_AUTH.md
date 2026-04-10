# Authentication Middleware (TK-9E0440)

## Overview

Authentication middleware validates API keys and loads customer configuration for downstream middleware and endpoints.

## Implementation

**File**: `app/middleware/auth.py`

### How it works

1. **Extract API key** from `X-API-Key` request header
2. **Validate** against `customers` table using bcrypt hash comparison
3. **Load customer config** into `request.state`:
   - `request.state.customer` - Full Customer model object
   - `request.state.customer_id` - Customer ID string
   - `request.state.budget_monthly_usd` - Monthly budget limit
   - `request.state.budget_daily_usd` - Daily budget limit
   - `request.state.rate_limit_tpm` - Tokens per minute limit
4. **Error handling**:
   - Missing key → 401 "Missing API key"
   - Invalid key → 401 "Invalid API key"
   - Database error → 401 "Authentication service unavailable" (fail closed)

### Security features

- **bcrypt hashing**: API keys stored as bcrypt hashes, not plaintext
- **Fail closed**: Database errors return 401 (deny access)
- **No timing attacks**: All customers checked before comparison (constant-time in aggregate)
- **Endpoint bypass**: `/health`, `/metrics`, `/` skip authentication

## Usage

### Adding a new customer

1. **Generate API key and hash**:
```bash
python scripts/generate_api_key_hash.py
```

2. **Insert into database**:
```sql
INSERT INTO customers (
    customer_id,
    api_key_hash,
    budget_monthly_usd,
    budget_daily_usd,
    rate_limit_tpm
) VALUES (
    'acme_corp',
    '$2b$12$...',  -- from generate_api_key_hash.py
    5000.00,
    200.00,
    500000
);
```

3. **Give API key to customer** (securely - only shown once!)

### Making authenticated requests

```bash
curl -H "X-API-Key: sk_abc123..." \
     https://api.example.com/v1/proxy/chat/completions
```

### For downstream middleware/endpoints

Customer config is available in `request.state`:

```python
@app.get("/example")
async def example(request: Request):
    customer_id = request.state.customer_id
    budget = request.state.budget_monthly_usd
    # ...
```

## Testing

**Run tests**:
```bash
pytest tests/test_auth.py -v
```

**Test coverage**:
- ✅ Missing API key returns 401
- ✅ Invalid API key returns 401
- ✅ Valid API key loads customer config
- ✅ Health/metrics endpoints bypass auth

## Dependencies

- `bcrypt==4.1.2` - Secure password hashing
- `sqlalchemy` - Database ORM
- `app.models.customer.Customer` - Customer model
- `app.errors.AuthenticationError` - Structured error response

## Integration points

**Upstream**: None (first middleware after logging)

**Downstream**:
- `RateLimitMiddleware` - Uses `request.state.customer_id` and `rate_limit_tpm`
- `BudgetMiddleware` - Uses `request.state.customer_id` and budget fields
- Proxy endpoint - Uses `request.state.customer_id` for audit trail

## Deployment notes

- **Migration**: Run `migrations/002_llm_requests_schema.sql` to create `customers` table
- **Test data**: Run `migrations/003_add_test_customer.sql` for dev/staging (NOT production!)
- **Test API key** (dev only): `test_api_key_dev_only`
- **Production**: Generate unique keys per customer using `generate_api_key_hash.py`

## Troubleshooting

**401 "Missing API key"**
- Check `X-API-Key` header is present
- Header name is case-sensitive

**401 "Invalid API key"**
- Verify key matches one in database
- Check for whitespace/newlines in key
- Ensure migration created customers table

**401 "Authentication service unavailable"**
- Check database connection (settings.database_url)
- Check database migration ran successfully
- Review logs for specific error

## Future enhancements

- [ ] Add API key rotation endpoint
- [ ] Add API key revocation
- [ ] Add per-key scopes/permissions
- [ ] Add API key expiration dates
- [ ] Add audit log for failed auth attempts
