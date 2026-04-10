# AI API Platform

Core infrastructure service for AI API request routing, authentication, rate limiting, and usage tracking.

## Architecture

See `/docs/architecture.md` for system design.

## Getting Started

```bash
pip install -r requirements.txt
python -m app.main
```

## Project Structure

- `app/` - Main application code
- `app/models/` - Database models
- `app/middleware/` - Rate limiting, auth, logging
- `app/routes/` - API endpoints
- `tests/` - Unit and integration tests
- `migrations/` - Database migrations
