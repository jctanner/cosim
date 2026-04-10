import pytest
from fastapi.testclient import TestClient
from unittest.mock import Mock, patch, MagicMock
import bcrypt
from app.main import app
from app.models.customer import Customer

client = TestClient(app)


@pytest.fixture
def mock_customer():
    """Create a mock customer with hashed API key"""
    customer = Mock(spec=Customer)
    customer.customer_id = "test_customer"

    # Create bcrypt hash for test API key "test_key_123"
    test_api_key = "test_key_123"
    customer.api_key_hash = bcrypt.hashpw(test_api_key.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    customer.budget_monthly_usd = 1000.00
    customer.budget_daily_usd = 50.00
    customer.rate_limit_tpm = 100000

    return customer, test_api_key


def test_missing_api_key():
    """Test that missing API key returns 401"""
    response = client.get("/v1/proxy/chat/completions")

    assert response.status_code == 401
    assert response.json()['error'] == 'authentication_failed'
    assert 'Missing API key' in response.json()['message']


@patch('app.middleware.auth.SessionLocal')
def test_invalid_api_key(mock_session_local):
    """Test that invalid API key returns 401"""
    # Mock database query to return empty list
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = []
    mock_session_local.return_value = mock_db

    response = client.get(
        "/v1/proxy/chat/completions",
        headers={"X-API-Key": "invalid_key"}
    )

    assert response.status_code == 401
    assert response.json()['error'] == 'authentication_failed'
    assert 'Invalid API key' in response.json()['message']


@patch('app.middleware.auth.SessionLocal')
def test_valid_api_key_loads_customer_config(mock_session_local, mock_customer):
    """Test that valid API key loads customer config into request.state"""
    customer, test_api_key = mock_customer

    # Mock database query to return our test customer
    mock_db = MagicMock()
    mock_db.query.return_value.all.return_value = [customer]
    mock_session_local.return_value = mock_db

    # Make request with valid API key
    # Note: This will fail at the proxy endpoint since it doesn't exist yet,
    # but auth middleware should succeed and load customer config
    response = client.get(
        "/v1/proxy/chat/completions",
        headers={"X-API-Key": test_api_key}
    )

    # Auth should pass (response will be 404 since proxy endpoint doesn't exist yet)
    # If auth failed, we'd get 401 instead
    assert response.status_code != 401


def test_health_endpoint_bypasses_auth():
    """Test that health endpoint doesn't require authentication"""
    response = client.get("/health")

    # Should succeed without X-API-Key header
    assert response.status_code == 200


def test_metrics_endpoint_bypasses_auth():
    """Test that metrics endpoint doesn't require authentication"""
    response = client.get("/metrics")

    # Should succeed without X-API-Key header
    assert response.status_code == 200
