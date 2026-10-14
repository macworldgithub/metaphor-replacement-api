import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_catalog_endpoint():
    response = client.get("/api/v1/metaphors/catalog")
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] > 0
    assert isinstance(data["items"], list)


def test_analyze_metaphor_literal():
    payload = {
        "text": "We need to bite the bullet and finish this task.",
        "style": "literal",
    }
    response = client.post("/api/v1/metaphors/analyze", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["metaphors_found"] >= 1
    assert "face the difficult situation bravely" in data["transformed_text"]


def test_replace_metaphor_custom_override():
    payload = {
        "text": "The meeting was a walk in the park.",
        "style": "literal",
        "custom_overrides": {
            "walk in the park": "very straightforward"
        }
    }
    response = client.post("/api/v1/metaphors/replace", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["transformed_text"] == "The meeting was a very straightforward."
    assert data["replacements_applied"] >= 1
