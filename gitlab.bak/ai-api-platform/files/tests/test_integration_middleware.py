import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app
from app.database import SessionLocal
from app.models.customer import Customer
from app.models.llm_request import LLMRequest
import bcrypt
import redis

@pytest.fixture
def mock_openai():
    """Mock OpenAI client to avoid external API dependency in tests"""
    with patch('app.routes.proxy.OpenAI') as mock:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.id = 'test-response-id'
        mock_response.model = 'gpt-4'
        mock_response.choices = [MagicMock(message=MagicMock(content='Test response'))]
        mock_response.usage = MagicMock(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30
        )
        mock_client.chat.completions.create.return_value = mock_response
        mock.return_value = mock_client
        yield mock_client

def test_middleware_flow_under_budget(mock_openai):
    """Test full middleware stack: auth → budget check → proxy → audit"""
    client = TestClient(app)
    
    # Setup: customer with budget headroom
    db = SessionLocal()
    customer = Customer(
        customer_id='test-customer',
        api_key_hash=bcrypt.hashpw('test-key'.encode(), bcrypt.gensalt()).decode(),
        budget_monthly_usd=100.00,
        budget_daily_usd=10.00,
        rate_limit_tpm=10000
    )
    db.add(customer)
    db.commit()
    
    # Setup: Redis budget state (under limit)
    r = redis.from_url('redis://localhost:6379/0')
    r.set('budget:test-customer:monthly', 50.00)  # Under $100 limit
    
    # Execute: send proxy request
    response = client.post(
        '/v1/llm/proxy',
        headers={'X-API-Key': 'test-key'},
        json={'model': 'gpt-4', 'messages': [{'role': 'user', 'content': 'test'}]}
    )
    
    # Validate: request succeeded
    assert response.status_code == 200
    
    # Validate: audit record created with correct schema
    audit = db.query(LLMRequest).filter_by(customer_id='test-customer').first()
    assert audit is not None
    assert audit.tokens_input == 10  # Correct field name
    assert audit.tokens_output == 20  # Not tokens_in/tokens_out
    assert audit.throttled == False
    
    db.close()

def test_middleware_flow_over_budget(mock_openai):
    """Test budget enforcement blocks over-limit requests"""
    client = TestClient(app)
    
    # Setup: customer at budget limit
    db = SessionLocal()
    customer = Customer(
        customer_id='test-customer-2',
        api_key_hash=bcrypt.hashpw('test-key-2'.encode(), bcrypt.gensalt()).decode(),
        budget_monthly_usd=100.00,
        rate_limit_tpm=10000
    )
    db.add(customer)
    db.commit()
    
    # Setup: Redis shows budget exceeded
    r = redis.from_url('redis://localhost:6379/0')
    r.set('budget:test-customer-2:monthly', 105.00)  # Over $100 limit
    
    # Execute: send proxy request
    response = client.post(
        '/v1/llm/proxy',
        headers={'X-API-Key': 'test-key-2'},
        json={'model': 'gpt-4', 'messages': [{'role': 'user', 'content': 'test'}]}
    )
    
    # Validate: request blocked with proper error
    assert response.status_code == 429
    assert 'budget' in response.json()['error']['message'].lower()
    
    # Validate: audit record shows throttled request
    audit = db.query(LLMRequest).filter_by(customer_id='test-customer-2').first()
    assert audit.throttled == True
    assert audit.throttle_reason == 'budget_exceeded'
    
    db.close()
