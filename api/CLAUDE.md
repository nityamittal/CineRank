# CLAUDE.md — CineRank: FastAPI Serving Layer

## Goal

Serve personalized movie recommendations via a REST API with < 100ms response time on cache hits. Also accept new user events and proxy them to Kafka.

## Implementation: `api/main.py`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check — verifies Redis and model are loaded |
| GET | `/recommendations` | Get personalized recs for a user |
| GET | `/similar` | Get similar movies to a given movie |
| GET | `/popular` | Get globally popular movies (optional genre filter) |
| POST | `/events` | Submit a new user event → Kafka |
| GET | `/user/{user_id}/profile` | Get user feature vector from Redis |
| GET | `/docs` | Auto-generated Swagger UI (FastAPI built-in) |

### Endpoint Details

#### `GET /recommendations?user_id=123&n=10`

```python
@app.get("/recommendations", response_model=RecommendationResponse)
async def get_recommendations(user_id: int, n: int = 10):
    start = time.perf_counter()
    recs = model.get_recommendations(user_id, n=n)
    latency_ms = (time.perf_counter() - start) * 1000
    return RecommendationResponse(
        user_id=user_id,
        recommendations=recs,
        source="cache" if was_cached else "computed",
        latency_ms=round(latency_ms, 2)
    )
```

Response example:
```json
{
  "user_id": 123,
  "recommendations": [
    {"movie_id": 318, "title": "Shawshank Redemption, The (1994)", "score": 0.952, "genres": "Crime|Drama"},
    {"movie_id": 858, "title": "Godfather, The (1972)", "score": 0.941, "genres": "Crime|Drama"}
  ],
  "source": "cache",
  "latency_ms": 2.34
}
```

#### `GET /similar?movie_id=318&n=10`

Returns movies most similar to the given movie based on learned item factors.

#### `POST /events`

Accepts a user event, validates it, and produces to Kafka:
```json
{
  "user_id": 123,
  "movie_id": 456,
  "rating": 4.5,
  "timestamp": null
}
```
If `timestamp` is null, set to `int(time.time())`.

#### `GET /user/{user_id}/profile`

Returns the user's real-time feature profile from Redis:
```json
{
  "user_id": 123,
  "recent_movies": [456, 789, 101],
  "avg_rating": 3.8,
  "event_count": 47,
  "top_genres": {"Drama": 15, "Action": 12, "Comedy": 8},
  "last_active": 1710000000
}
```

### Startup / Shutdown

```python
@app.on_event("startup")
async def startup():
    # 1. Connect to Redis (with retry)
    # 2. Load recommendation model into memory
    # 3. Create Kafka producer (for /events endpoint)
    # 4. Log startup time and model metadata

@app.on_event("shutdown")
async def shutdown():
    # 1. Flush Kafka producer
    # 2. Close Redis connection
```

### Middleware

Add timing middleware that logs every request:
```python
@app.middleware("http")
async def add_timing_header(request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration:.2f}"
    return response
```

### CORS

Enable CORS for local development:
```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## `api/schemas.py`

```python
from pydantic import BaseModel, Field
from typing import List, Optional

class UserEvent(BaseModel):
    user_id: int = Field(..., gt=0)
    movie_id: int = Field(..., gt=0)
    rating: float = Field(..., ge=0.5, le=5.0)
    timestamp: Optional[int] = None

class MovieRecommendation(BaseModel):
    movie_id: int
    title: str
    score: float
    genres: str

class RecommendationResponse(BaseModel):
    user_id: int
    recommendations: List[MovieRecommendation]
    source: str  # "cache", "computed", "popular_fallback"
    latency_ms: float

class UserProfile(BaseModel):
    user_id: int
    recent_movies: List[int]
    avg_rating: Optional[float]
    event_count: int
    top_genres: dict
    last_active: Optional[int]

class HealthResponse(BaseModel):
    status: str
    redis_connected: bool
    model_loaded: bool
    model_info: dict

class EventResponse(BaseModel):
    status: str
    message: str
```

---

## Configuration

All via environment variables:
```
REDIS_HOST=redis          (default: localhost)
REDIS_PORT=6379
KAFKA_BOOTSTRAP_SERVERS=kafka:29092  (default: localhost:9092)
MODEL_DIR=./models
API_HOST=0.0.0.0
API_PORT=8000
```

## Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

Use `--workers 1` since the model is loaded in memory. For production, use multiple workers with shared model or switch to gunicorn.

## requirements.txt

```
fastapi>=0.104.0
uvicorn>=0.24.0
redis>=5.0.0
confluent-kafka>=2.3.0
numpy>=1.24.0
psycopg2-binary>=2.9.0
pydantic>=2.5.0
```

## Verification

```bash
# Start the API
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Health check
curl http://localhost:8000/health

# Get recommendations
curl "http://localhost:8000/recommendations?user_id=1&n=5"

# Submit an event
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "movie_id": 318, "rating": 5.0}'

# User profile
curl http://localhost:8000/user/1/profile

# Similar movies
curl "http://localhost:8000/similar?movie_id=318&n=5"

# Swagger docs
open http://localhost:8000/docs
```

## Performance Expectations

- `/recommendations` (cache hit): 1–5ms
- `/recommendations` (cache miss, compute): 50–200ms
- `/similar`: 10–50ms
- `/events`: 5–20ms (Kafka produce is async)
- `/user/{id}/profile`: 2–10ms (Redis reads)
