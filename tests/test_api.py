"""Tests for the FastAPI serving layer."""

import json
import sys
import os

import pytest

# Add project root and module paths so both `api.main` and `recommendation_engine` resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recommendation_engine"))


class MockRedis:
    """In-memory Redis stub for testing."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list] = {}
        self.hashes: dict[str, dict] = {}

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        pass

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    def exists(self, key: str) -> int:
        return 1 if key in self.store else 0

    def lrange(self, key: str, start: int, stop: int) -> list:
        return self.lists.get(key, [])[start : stop + 1 if stop >= 0 else None]

    def hgetall(self, key: str) -> dict:
        return self.hashes.get(key, {})


class MockModel:
    """Mock recommendation model for testing."""

    def __init__(self) -> None:
        self.loaded = True
        self.metadata = {"n_users": 100, "n_items": 50, "n_components": 10}
        self.user_map = {1: 0, 2: 1, 3: 2}

    def get_recommendations(self, user_id: int, n: int = 10, exclude_seen: bool = True) -> list[dict]:
        if user_id in self.user_map:
            return [
                {"movie_id": 318, "title": "Shawshank Redemption, The (1994)", "score": 0.95, "genres": "Crime|Drama"},
                {"movie_id": 858, "title": "Godfather, The (1972)", "score": 0.91, "genres": "Crime|Drama"},
            ][:n]
        return [
            {"movie_id": 1, "title": "Toy Story (1995)", "score": 0.8, "genres": "Adventure|Animation|Children|Comedy|Fantasy"},
        ][:n]

    def get_similar_movies(self, movie_id: int, n: int = 10) -> list[dict]:
        if movie_id == 318:
            return [
                {"movie_id": 858, "title": "Godfather, The (1972)", "score": 0.88, "genres": "Crime|Drama"},
            ][:n]
        return []

    def get_popular_movies(self, n: int = 10, genre: str | None = None) -> list[dict]:
        return [
            {"movie_id": 1, "title": "Toy Story (1995)", "score": 0.7, "genres": "Adventure|Animation|Children|Comedy|Fantasy"},
        ][:n]


class MockProducer:
    """Mock Kafka producer."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    def produce(self, topic: str, key: bytes = b"", value: bytes = b"", callback=None) -> None:
        self.messages.append({"topic": topic, "key": key, "value": value})

    def poll(self, timeout: float = 0) -> None:
        pass

    def flush(self, timeout: float = 5) -> None:
        pass

    def list_topics(self, timeout: float = 5) -> None:
        pass


@pytest.fixture
def client():
    """Create a test client with mocked dependencies."""
    mock_redis = MockRedis()
    mock_model = MockModel()
    mock_producer = MockProducer()

    # Patch the module-level globals before importing
    import api.main as api_main
    api_main.redis_client = mock_redis
    api_main.rec_model = mock_model
    api_main.kafka_producer = mock_producer

    from fastapi.testclient import TestClient
    with TestClient(api_main.app) as c:
        yield c


def test_health(client) -> None:
    """GET /health returns 200 with status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["redis_connected"] is True
    assert data["model_loaded"] is True


def test_recommendations_valid_user(client) -> None:
    """GET /recommendations?user_id=1 returns recommendations."""
    resp = client.get("/recommendations?user_id=1&n=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == 1
    assert len(data["recommendations"]) > 0
    assert "movie_id" in data["recommendations"][0]
    assert "title" in data["recommendations"][0]
    assert "score" in data["recommendations"][0]
    assert "latency_ms" in data


def test_recommendations_missing_user_id(client) -> None:
    """GET /recommendations without user_id returns 422."""
    resp = client.get("/recommendations")
    assert resp.status_code == 422


def test_recommendations_unknown_user(client) -> None:
    """GET /recommendations for unknown user returns popular fallback."""
    resp = client.get("/recommendations?user_id=999999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "popular_fallback"
    assert len(data["recommendations"]) > 0


def test_similar_movies(client) -> None:
    """GET /similar?movie_id=318 returns similar movies."""
    resp = client.get("/similar?movie_id=318&n=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["movie_id"] == 318
    assert len(data["similar"]) > 0


def test_similar_movies_not_found(client) -> None:
    """GET /similar for unknown movie returns 404."""
    resp = client.get("/similar?movie_id=999999")
    assert resp.status_code == 404


def test_post_event_valid(client) -> None:
    """POST /events with valid body returns 200."""
    resp = client.post(
        "/events",
        json={"user_id": 1, "movie_id": 318, "rating": 5.0},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"


def test_post_event_invalid_rating(client) -> None:
    """POST /events with rating > 5.0 returns 422."""
    resp = client.post(
        "/events",
        json={"user_id": 1, "movie_id": 318, "rating": 6.0},
    )
    assert resp.status_code == 422


def test_post_event_invalid_rating_low(client) -> None:
    """POST /events with rating < 0.5 returns 422."""
    resp = client.post(
        "/events",
        json={"user_id": 1, "movie_id": 318, "rating": 0.0},
    )
    assert resp.status_code == 422


def test_popular_movies(client) -> None:
    """GET /popular returns popular movies."""
    resp = client.get("/popular?n=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["movies"]) > 0


def test_user_profile_not_found(client) -> None:
    """GET /user/{id}/profile for unknown user returns 404."""
    resp = client.get("/user/999999/profile")
    assert resp.status_code == 404
