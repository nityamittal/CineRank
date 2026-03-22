# CLAUDE.md — CineRank: Tests

## Goal

Write tests that verify each component works correctly, both in isolation (unit) and together (integration).

## Test Framework

- `pytest` for all tests
- `httpx` for async FastAPI testing via `TestClient`
- No mocking frameworks needed — use simple stubs where necessary

## File: `tests/test_api.py`

Test the FastAPI endpoints. Use `TestClient` from `fastapi.testclient`.

```python
from fastapi.testclient import TestClient

# Override dependencies for testing:
# - Use a fake Redis (or a real Redis if available)
# - Use a mock RecommendationModel that returns fixed results

class MockModel:
    def get_recommendations(self, user_id, n=10, exclude_seen=True):
        return [{"movie_id": 1, "title": "Test Movie", "score": 0.9, "genres": "Drama"}]
    def get_similar_movies(self, movie_id, n=10):
        return [{"movie_id": 2, "title": "Similar Movie", "score": 0.8, "genres": "Action"}]
    def get_popular_movies(self, n=10, genre=None):
        return [{"movie_id": 3, "title": "Popular Movie", "score": 0.7, "genres": "Comedy"}]
```

### Tests

1. `test_health` — GET `/health` returns 200 and `status: "ok"`
2. `test_recommendations_valid_user` — GET `/recommendations?user_id=1` returns 200 with a list of movies
3. `test_recommendations_missing_user_id` — GET `/recommendations` (no user_id) returns 422
4. `test_similar_movies` — GET `/similar?movie_id=318` returns 200
5. `test_post_event_valid` — POST `/events` with valid body returns 200
6. `test_post_event_invalid_rating` — POST `/events` with rating=6.0 returns 422
7. `test_user_profile` — GET `/user/1/profile` returns profile or 404

---

## File: `tests/test_producer.py`

Unit tests for the producer's serialization logic. No Kafka needed.

### Tests

1. `test_event_serialization` — verify a DataFrame row converts to expected JSON format
2. `test_event_key_encoding` — verify user_id is properly encoded as bytes
3. `test_limit_flag` — verify producer respects `--limit` argument
4. `test_invalid_csv_handling` — verify graceful error on missing file

---

## File: `tests/test_processor.py`

Unit tests for the stream processor's feature extraction logic. Mock Redis with a simple dict-based stub.

```python
class FakeRedis:
    """In-memory Redis stub for testing."""
    def __init__(self):
        self.store = {}
        self.lists = {}
        self.hashes = {}
    
    def pipeline(self):
        return FakePipeline(self)
    # ... implement lpush, ltrim, incr, hincrby, set, get, etc.
```

### Tests

1. `test_process_single_event` — process one event, verify all 6 Redis keys are set
2. `test_recent_movies_max_20` — process 25 events, verify list length is 20
3. `test_genre_counts` — process events for movies with known genres, verify counts
4. `test_avg_rating_calculation` — process 3 events with ratings 3, 4, 5 → avg should be 4.0
5. `test_multiple_users` — process events for different users, verify isolation

---

## File: `tests/test_model.py`

Test the recommendation engine logic.

### Tests

1. `test_model_loads` — RecommendationModel initializes without error when model files exist
2. `test_get_recommendations_known_user` — returns a non-empty list with expected fields
3. `test_get_recommendations_unknown_user` — returns popular movies fallback
4. `test_similar_movies` — returns similar movies, excludes the query movie itself
5. `test_cosine_similarity` — verify the utility function with known vectors

---

## requirements-test.txt

```
pytest>=7.4.0
httpx>=0.25.0
pytest-asyncio>=0.23.0
```

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific file
pytest tests/test_api.py -v

# With coverage
pip install pytest-cov
pytest tests/ --cov=api --cov=stream_processor --cov=recommendation_engine -v
```

## CI Note

Tests that need real Kafka/Redis should be marked with `@pytest.mark.integration` and skipped in CI unless those services are available:

```python
import pytest
import os

requires_redis = pytest.mark.skipif(
    os.environ.get("REDIS_HOST") is None,
    reason="Redis not available"
)
```
