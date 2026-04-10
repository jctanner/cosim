import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

def test_completions_endpoint():
    response = client.post("/v1/completions", json={
        "model": "gpt-3.5-turbo",
        "prompt": "Hello",
        "max_tokens": 50
    })
    assert response.status_code == 200
    assert "choices" in response.json()
