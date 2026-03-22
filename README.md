# CineRank — Real-Time Personalized Movie Recommendation System using Kafka, Redis, and FastAPI

![Python](https://img.shields.io/badge/Python-3.11-blue)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Kafka](https://img.shields.io/badge/Kafka-Streaming-orange)
![FastAPI](https://img.shields.io/badge/FastAPI-REST-teal)

A fully working, deployable real-time movie recommendation system that ingests user activity via Kafka, processes it in real time, stores features in Redis, trains a collaborative filtering model, and serves personalized movie recommendations via FastAPI. Built entirely with free and open-source tools on the MovieLens 32M dataset.

---

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│  ratings.csv │────▶│    Kafka     │────▶│ Stream Processor │
│  (MovieLens) │     │  Producer    │     │   (Consumer)     │
└──────────────┘     └──────┬───────┘     └────────┬─────────┘
                            │                      │
                            ▼                      ▼
                    ┌──────────────┐       ┌──────────────┐
                    │    Kafka     │       │    Redis     │
                    │ user_events  │       │  (Features   │
                    │   topic      │       │   + Cache)   │
                    └──────────────┘       └──────┬───────┘
                                                  │
┌──────────────┐                                  │
│  PostgreSQL  │     ┌──────────────┐             │
│  (Movies,    │────▶│   FastAPI    │◀────────────┘
│  Ratings)    │     │   /recs      │
└──────────────┘     │   /similar   │──────▶  Client
                     │   /events    │
  ┌──────────────┐   └──────────────┘
  │ SVD Model    │──────────┘
  │ (50 factors) │
  └──────────────┘
```

**Data Flow:**
1. `ratings.csv` is replayed through a Kafka producer into the `user_events` topic
2. A stream processor consumes events, computes per-user features, and writes to Redis
3. A TruncatedSVD model is trained offline on the user-item matrix
4. FastAPI serves recommendations by combining the model with real-time features from Redis
5. New user events can be submitted via the API and flow back through Kafka

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/yourusername/cinerank.git && cd cinerank

# 2. Download MovieLens 32M dataset into ml-32m/ (if not present)
# https://grouplens.org/datasets/movielens/32m/

# 3. Start infrastructure
docker-compose up -d

# 4. Seed the database and train the model
python scripts/seed_postgres.py --max-ratings 1000000
python recommendation_engine/train_model.py --csv-path ./ml-32m/ratings.csv --max-ratings 1000000

# 5. Start all services
docker-compose up --build
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for the interactive Swagger UI.

---

## Detailed Setup

### Prerequisites

- Docker & Docker Compose
- Python 3.11+ (for local development/training)
- ~2 GB disk space for MovieLens 32M dataset

### Dataset

The MovieLens 32M dataset should be placed in `ml-32m/` at the project root:

```
ml-32m/
├── ratings.csv    # 32M ratings from 200K users across 87K movies
├── movies.csv     # Movie titles and genres
├── tags.csv       # User-generated tags
└── links.csv      # IMDb and TMDb IDs
```

Download from [GroupLens](https://grouplens.org/datasets/movielens/32m/).

### Configuration

All configuration is via environment variables in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `kafka:29092` | Kafka broker (internal Docker) |
| `REDIS_HOST` | `redis` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `POSTGRES_HOST` | `postgres` | PostgreSQL hostname |
| `POSTGRES_DB` | `recdb` | Database name |
| `POSTGRES_USER` | `recuser` | Database user |
| `POSTGRES_PASSWORD` | `recpass` | Database password |

---

## API Reference

### `GET /health`

Health check. Returns Redis and model status.

```bash
curl http://localhost:8000/health
```

```json
{
  "status": "ok",
  "redis_connected": true,
  "model_loaded": true,
  "model_info": {"n_users": 200948, "n_items": 87585, "n_components": 50}
}
```

### `GET /recommendations?user_id=123&n=10`

Get personalized movie recommendations for a user.

```bash
curl "http://localhost:8000/recommendations?user_id=1&n=5"
```

```json
{
  "user_id": 1,
  "recommendations": [
    {"movie_id": 318, "title": "Shawshank Redemption, The (1994)", "score": 0.952, "genres": "Crime|Drama"},
    {"movie_id": 858, "title": "Godfather, The (1972)", "score": 0.941, "genres": "Crime|Drama"}
  ],
  "source": "cache",
  "latency_ms": 2.34
}
```

### `GET /similar?movie_id=318&n=10`

Get movies similar to a given movie.

```bash
curl "http://localhost:8000/similar?movie_id=318&n=5"
```

### `GET /popular?n=10&genre=Drama`

Get globally popular movies, optionally filtered by genre.

```bash
curl "http://localhost:8000/popular?n=5&genre=Sci-Fi"
```

### `POST /events`

Submit a new user event (produces to Kafka).

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "movie_id": 318, "rating": 5.0}'
```

### `GET /user/{user_id}/profile`

Get a user's real-time feature profile from Redis.

```bash
curl http://localhost:8000/user/1/profile
```

```json
{
  "user_id": 1,
  "recent_movies": [318, 260, 50],
  "avg_rating": 4.2,
  "event_count": 47,
  "top_genres": {"Drama": 15, "Action": 12, "Comedy": 8},
  "last_active": 1710000000
}
```

---

## System Design

### Scalability

- **Kafka partitioning**: Events are keyed by `user_id`, ensuring all events for a user land on the same partition — this allows parallel processing with consistent per-user state
- **Redis as feature store**: O(1) lookups for user features and cached recommendations
- **Precomputed recs**: Top 1000 users have precomputed recommendations; only cache misses require real-time computation
- **Horizontal scaling**: Multiple stream processors can consume from different Kafka partitions

### Fault Tolerance

- **Service retry**: All components retry connections to Kafka and Redis on startup with exponential backoff
- **Graceful shutdown**: SIGINT/SIGTERM handlers ensure clean Kafka offset commits and connection closure
- **Healthchecks**: Docker Compose uses health-based `depends_on` for correct startup ordering
- **Cold-start fallback**: Unknown users get popular movies; the system never returns empty responses

### Performance Targets

| Operation | Target | Mechanism |
|-----------|--------|-----------|
| Event → feature update | < 1 second | Kafka consumer + Redis pipeline |
| API response (cache hit) | < 100ms | Precomputed recs in Redis |
| API response (cache miss) | < 500ms | Real-time dot product computation |
| Event ingestion throughput | 1000+ events/sec | Confluent Kafka producer batching |

---

## Tech Stack

| Layer | Tool | Purpose |
|-------|------|---------|
| Streaming | Apache Kafka (Docker) | Event ingestion and distribution |
| Stream Processing | confluent-kafka Consumer | Real-time feature extraction |
| Cache / Feature Store | Redis | User features + recommendation cache |
| API | FastAPI + Uvicorn | REST API for recommendations |
| ML / Recommendations | scikit-learn (TruncatedSVD) | Collaborative filtering |
| Database | PostgreSQL | Movie metadata + historical interactions |
| Deployment | Docker + Docker Compose | Container orchestration |
| Dataset | MovieLens 32M | 32M ratings, 200K users, 87K movies |

---

## Project Structure

```
cinerank/
├── docker-compose.yml          # Full infrastructure + app stack
├── .env                        # Environment variables
├── api/                        # FastAPI serving layer
│   ├── main.py                 # Endpoints and middleware
│   ├── schemas.py              # Pydantic models
│   ├── Dockerfile
│   └── requirements.txt
├── kafka_producer/             # Event replay from CSV
│   ├── produce_events.py
│   ├── Dockerfile
│   └── requirements.txt
├── stream_processor/           # Real-time feature extraction
│   ├── app.py
│   ├── Dockerfile
│   └── requirements.txt
├── recommendation_engine/      # SVD model training + inference
│   ├── train_model.py
│   ├── model.py
│   ├── utils.py
│   ├── Dockerfile
│   └── requirements.txt
├── db/
│   └── init.sql                # PostgreSQL schema
├── scripts/
│   ├── seed_postgres.py        # Load MovieLens into PostgreSQL
│   ├── seed_redis.py           # Optional Redis cache warmup
│   └── init_kafka_topics.sh    # Create Kafka topics
├── tests/                      # pytest test suite
│   ├── test_api.py
│   ├── test_producer.py
│   ├── test_processor.py
│   └── test_model.py
├── data/
│   └── sample_events.json      # 10 test events
└── ml-32m/                     # MovieLens 32M dataset (gitignored)
```

---

## Running Tests

```bash
pip install -r requirements-test.txt
pip install -r api/requirements.txt
pip install -r recommendation_engine/requirements.txt

pytest tests/ -v
```

All tests run without external services (Kafka, Redis, PostgreSQL are fully mocked).

---

## Possible Extensions

- **A/B testing framework**: Route users to different models and track metrics
- **Grafana dashboard**: Monitor Kafka lag, Redis hit rates, API latency
- **Deep learning embeddings**: Replace SVD with neural collaborative filtering
- **Content-based features**: Incorporate movie descriptions, tags, and poster images
- **Real-time model updates**: Incrementally update user factors as new events arrive
- **Multi-armed bandit**: Explore/exploit tradeoff for new items

---

## Resume Bullet

> Built CineRank, a real-time movie recommendation system using Kafka, Redis, and FastAPI that processes streaming user activity from MovieLens 32M and serves low-latency personalized recommendations using a fully open-source stack.

---

## License

MIT
