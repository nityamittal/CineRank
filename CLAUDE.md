# CineRank — Real-Time Personalized Movie Recommendation System using Kafka, Redis, and FastAPI

## Project Overview

Build a fully working, deployable real-time movie recommendation system (CineRank) using the MovieLens dataset. The system ingests user activity via Kafka, processes it in real time, stores features in Redis, trains a recommendation model, and serves personalized movie recommendations via a FastAPI endpoint. **Every tool used must be free and open-source — no paid APIs.**

## Architecture

```
ratings.csv → Kafka Producer → Kafka (user_events topic)
    → Stream Processor (Faust) → Redis (user features + cached recs)
    → Recommendation Engine (scikit-learn / Faiss)
    → FastAPI API → Client
    
PostgreSQL stores user profiles, movie metadata, and historical interactions.
```

## Tech Stack (All Free)

| Layer              | Tool                          |
|--------------------|-------------------------------|
| Streaming          | Apache Kafka (via Docker)     |
| Stream Processing  | Faust (Python Kafka Streams)  |
| Cache / Features   | Redis                         |
| API                | FastAPI + Uvicorn             |
| ML / Recs          | scikit-learn, scipy, Faiss    |
| Database           | PostgreSQL                    |
| Deployment         | Docker + Docker Compose       |
| Dataset            | MovieLens 32M (already downloaded in ml-32m/) |

## Project Structure

```
cinerank/
├── CLAUDE.md                        # This file
├── docker-compose.yml               # Full infra stack
├── .env                             # Environment variables
├── .env.example                     # Template
├── README.md                        # Project documentation
│
├── data/
│   └── sample_events.json           # Small test payload
│
├── ml-32m/                          # MovieLens 32M dataset (already present, gitignored)
│   ├── ratings.csv
│   ├── movies.csv
│   ├── tags.csv
│   └── links.csv
│
├── kafka_producer/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── produce_events.py            # Replays ratings.csv → Kafka
│
├── stream_processor/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app.py                       # Faust app: consume → extract features → write Redis
│
├── recommendation_engine/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── train_model.py               # Batch: build user-item matrix, compute SVD
│   ├── model.py                     # Load trained model, generate predictions
│   └── utils.py                     # Cosine similarity, genre enrichment
│
├── api/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                      # FastAPI app
│   └── schemas.py                   # Pydantic request/response models
│
├── db/
│   └── init.sql                     # PostgreSQL schema: users, movies, interactions
│
├── scripts/
│   ├── init_kafka_topics.sh         # Create Kafka topics
│   ├── seed_postgres.py              # Load MovieLens into PostgreSQL
│   └── seed_redis.py                # Optional: preload Redis cache
│
└── tests/
    ├── test_api.py
    ├── test_producer.py
    └── test_processor.py
```

## Build Instructions

Follow these phases in order. Each phase should be fully working before moving to the next.

---

### Phase 1: Infrastructure (Docker Compose)

Create `docker-compose.yml` with these services:

1. **zookeeper** — `confluentinc/cp-zookeeper:7.5.0`, port 2181
2. **kafka** — `confluentinc/cp-kafka:7.5.0`, port 9092, depends on zookeeper
   - `KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092`
   - `KAFKA_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,PLAINTEXT_HOST:PLAINTEXT`
   - `KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR: 1`
3. **redis** — `redis:7-alpine`, port 6379
4. **postgres** — `postgres:16-alpine`, port 5432, mount `./db/init.sql` to `/docker-entrypoint-initdb.d/`
   - `POSTGRES_DB=recdb`, `POSTGRES_USER=recuser`, `POSTGRES_PASSWORD=recpass`

Create `.env` and `.env.example`:
```
KAFKA_BOOTSTRAP_SERVERS=kafka:29092
KAFKA_BOOTSTRAP_SERVERS_LOCAL=localhost:9092
REDIS_HOST=redis
REDIS_PORT=6379
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=recdb
POSTGRES_USER=recuser
POSTGRES_PASSWORD=recpass
```

Create `db/init.sql`:
```sql
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS movies (
    movie_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    genres TEXT
);

CREATE TABLE IF NOT EXISTS interactions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(user_id),
    movie_id INTEGER REFERENCES movies(movie_id),
    rating FLOAT NOT NULL,
    timestamp BIGINT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_interactions_user ON interactions(user_id);
CREATE INDEX idx_interactions_movie ON interactions(movie_id);
```

Create `scripts/init_kafka_topics.sh`:
```bash
#!/bin/bash
kafka-topics --create --if-not-exists --topic user_events --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
kafka-topics --create --if-not-exists --topic processed_features --bootstrap-server kafka:29092 --partitions 3 --replication-factor 1
echo "Kafka topics created."
```

**Verify:** `docker-compose up -d` starts all 4 services healthy.

---

### Phase 2: Dataset & Seeding

The MovieLens 32M dataset already exists at `ml-32m/` in the project root with:
- `ratings.csv` — 32M ratings from 200,948 users across 87,585 movies
- `movies.csv` — movie titles and pipe-separated genres
- `tags.csv` — 2M user-generated tags
- `links.csv` — IMDb and TMDb IDs

**Do NOT create a download script.** The data is already present.

Add `ml-32m/` to `.gitignore`.

Create `scripts/seed_postgres.py` — reads `ml-32m/movies.csv` and `ml-32m/ratings.csv`, bulk-inserts into PostgreSQL using psycopg2. Load a subset (first 1M ratings) for dev mode. See `data/CLAUDE.md` for full spec.

Create `data/sample_events.json` with 10 hand-crafted test events.

**Verify:** After seeding, `SELECT COUNT(*) FROM interactions;` returns rows.

---

### Phase 3: Kafka Producer (Event Replay)

Create `kafka_producer/produce_events.py`:

- Read `ratings.csv`, sort by timestamp
- For each row, produce a JSON message to `user_events` topic:
  ```json
  {"user_id": 123, "movie_id": 456, "rating": 4.0, "timestamp": 964982703}
  ```
- Use `confluent-kafka` Python client
- Add `--speed` flag: `realtime` (use actual timestamp gaps), `fast` (10ms delay), `burst` (no delay)
- Add `--limit N` flag to cap number of events for testing
- Log progress every 10,000 events

`kafka_producer/requirements.txt`:
```
confluent-kafka>=2.3.0
pandas>=2.0.0
```

Create Dockerfile:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "produce_events.py", "--speed", "fast", "--limit", "100000"]
```

**Verify:** Run producer, then consume from `user_events` topic and see JSON messages.

---

### Phase 4: Stream Processor

Create `stream_processor/app.py` using **Faust** (Python stream processing library, Kafka Streams equivalent):

- Consume from `user_events`
- Per user, maintain rolling features in Redis:
  - `user:{user_id}:recent_movies` → list of last 20 movie_ids
  - `user:{user_id}:avg_rating` → running average
  - `user:{user_id}:genre_counts` → hash of genre → count (enrich from movies table or Redis lookup)
  - `user:{user_id}:event_count` → total interactions
- Write processed features to Redis with TTL of 24 hours
- Optionally produce to `processed_features` topic for downstream consumers

`stream_processor/requirements.txt`:
```
faust-streaming>=0.10.0
redis>=5.0.0
psycopg2-binary>=2.9.0
```

**If Faust causes issues**, fall back to a simple `confluent-kafka` consumer loop — the key is that it reads from Kafka and writes features to Redis in real time.

**Verify:** After producer sends events, check Redis: `redis-cli GET user:123:avg_rating` returns a value.

---

### Phase 5: Recommendation Engine

Create `recommendation_engine/train_model.py`:

1. Load interactions from PostgreSQL (or from CSV directly)
2. Build a sparse user-item rating matrix (scipy.sparse)
3. Apply TruncatedSVD (scikit-learn) with 50 latent factors
4. Save:
   - User factors matrix → `models/user_factors.npy`
   - Item factors matrix → `models/item_factors.npy`
   - User ID mapping → `models/user_map.json`
   - Item ID mapping → `models/item_map.json`
5. Precompute top-N recommendations for active users and cache in Redis:
   - Key: `recs:{user_id}` → JSON list of `[{movie_id, score}, ...]`

Create `recommendation_engine/model.py`:
- `get_recommendations(user_id, n=10)`:
  1. Check Redis cache first (`recs:{user_id}`)
  2. If miss: load user factor, compute dot product with all item factors, rank, return top-N
  3. Cache result in Redis with 1-hour TTL
- `get_similar_movies(movie_id, n=10)`:
  1. Cosine similarity on item factors
  2. Return top-N similar movies

Create `recommendation_engine/utils.py`:
- Genre parsing from movies.csv
- Cosine similarity helper
- Content-based fallback for cold-start users (recommend popular movies in preferred genres)

`recommendation_engine/requirements.txt`:
```
numpy>=1.24.0
scipy>=1.11.0
scikit-learn>=1.3.0
pandas>=2.0.0
redis>=5.0.0
psycopg2-binary>=2.9.0
```

**Verify:** `python train_model.py` completes, files appear in `models/`, Redis has cached recs.

---

### Phase 6: FastAPI Serving Layer

Create `api/main.py`:

```python
# Endpoints:
# GET  /health                          → {"status": "ok"}
# GET  /recommendations?user_id=123&n=10 → list of recommended movies with scores
# GET  /similar?movie_id=456&n=10       → list of similar movies
# POST /events                          → accepts a user event, produces to Kafka
# GET  /user/{user_id}/profile          → user features from Redis
```

Implementation:
- On startup: connect to Redis, load model factors into memory
- `/recommendations`: call `model.get_recommendations()`, enrich with movie titles from Redis/Postgres
- `/similar`: call `model.get_similar_movies()`, enrich with titles
- `/events`: validate with Pydantic schema, produce to Kafka `user_events` topic
- `/user/{user_id}/profile`: return feature vector from Redis

Create `api/schemas.py`:
```python
from pydantic import BaseModel
from typing import List, Optional

class UserEvent(BaseModel):
    user_id: int
    movie_id: int
    rating: float
    timestamp: Optional[int] = None  # auto-set if missing

class MovieRecommendation(BaseModel):
    movie_id: int
    title: str
    score: float
    genres: str

class RecommendationResponse(BaseModel):
    user_id: int
    recommendations: List[MovieRecommendation]
    source: str  # "cache" or "computed"
    latency_ms: float
```

`api/requirements.txt`:
```
fastapi>=0.104.0
uvicorn>=0.24.0
redis>=5.0.0
confluent-kafka>=2.3.0
numpy>=1.24.0
psycopg2-binary>=2.9.0
```

Dockerfile:
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**Verify:** `curl http://localhost:8000/recommendations?user_id=123` returns JSON with movie recommendations.

---

### Phase 7: Integration & Docker Compose Full Stack

Update `docker-compose.yml` to add application services:

- `kafka-init` — one-shot container that runs `init_kafka_topics.sh`, depends on kafka
- `producer` — builds from `kafka_producer/`, depends on kafka-init, mounts `data/`
- `processor` — builds from `stream_processor/`, depends on kafka, redis
- `api` — builds from `api/`, depends on redis, postgres, exposes port 8000

Add a `volumes` section for model persistence between runs.

**Verify:** `docker-compose up --build` starts everything. After ~30 seconds, the API serves recommendations.

---

### Phase 8: Tests

Create `tests/test_api.py`:
- Test `/health` returns 200
- Test `/recommendations?user_id=1` returns valid JSON with movie list
- Test `/events` POST accepts valid event
- Test `/recommendations` for unknown user returns fallback (popular movies)

Create `tests/test_producer.py`:
- Test that producer can serialize and send a message

Create `tests/test_processor.py`:
- Test feature extraction logic (unit test, no Kafka needed)

Use `pytest`. Add `requirements-test.txt`:
```
pytest>=7.4.0
httpx>=0.25.0  # for FastAPI TestClient
```

---

### Phase 9: README.md

Write a comprehensive README with:

1. **Title + badges** — "CineRank — Real-Time Personalized Movie Recommendation System using Kafka, Redis, and FastAPI" with Python, Docker, License badges
2. **Architecture diagram** (ASCII)
3. **Quick Start** — 5 commands to get running
4. **Detailed Setup** — prerequisites, dataset download, configuration
5. **API Reference** — all endpoints with examples (curl)
6. **System Design** — scalability, fault tolerance, performance targets
7. **Tech stack table**
8. **Extensions** — A/B testing, Grafana dashboard, embeddings
9. **Resume bullet** — "Built CineRank, a real-time movie recommendation system using Kafka, Redis, and FastAPI that processes streaming user activity from MovieLens 32M and serves low-latency personalized recommendations using a fully open-source stack."

---

## Code Quality Rules

- Use type hints everywhere in Python
- Add docstrings to all public functions
- Use `logging` module, not `print()`
- Handle errors gracefully — no bare `except:`
- All config via environment variables (never hardcoded)
- Use connection pools for Redis and PostgreSQL
- Add retry logic for Kafka connections (services may start at different times)

## Performance Targets

- Event ingestion to feature update: < 1 second
- API response time: < 100ms (cache hit), < 500ms (cache miss)
- Throughput: 1000+ events/sec locally

## Key Design Decisions

- **Faust over Flink**: Faust is pure Python, much simpler to set up locally than a JVM-based Flink cluster. For a portfolio project, this keeps the barrier to entry low while demonstrating the same streaming concepts.
- **SVD over deep learning**: TruncatedSVD is fast, interpretable, and works well on MovieLens. No GPU needed.
- **Redis as both feature store and rec cache**: Simplifies the architecture. In production you might separate these.
- **MovieLens 32M**: Large enough to be realistic, small enough to train locally in minutes.
