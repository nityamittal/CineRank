# CineRank Code Tour — What Every File Does

A file-by-file reference for reading the codebase. Concepts are explained in
[BEGINNER_GUIDE.md](BEGINNER_GUIDE.md); setup lives in
[RUNNING_LOCALLY.md](RUNNING_LOCALLY.md).

```
cinerank/
├── docker-compose.yml        # Defines and wires up all 8 containers
├── db/init.sql               # Postgres schema, auto-applied on first boot
├── kafka_producer/           # Replays ratings.csv into Kafka
├── stream_processor/         # Kafka → per-user features → Redis
├── recommendation_engine/    # SVD training + inference library
├── api/                      # FastAPI HTTP layer
├── scripts/                  # One-off seeding/setup helpers
├── tests/                    # pytest suite (no services required)
├── data/sample_events.json   # 10 hand-written events for quick testing
└── ml-32m/                   # The dataset (gitignored, you download it)
```

---

## Infrastructure

### `docker-compose.yml`

Defines every service and the order they start in. Key ideas:

- **Healthchecks + `depends_on: condition: service_healthy`** — the
  producer must not start before Kafka is genuinely accepting connections
  (which is later than "the container started"). Healthchecks close that gap.
- **`kafka-init`** is a one-shot container: it creates the topics and exits.
  Services that need topics depend on `condition: service_completed_successfully`.
- **Named volumes** (`model-data`, `postgres-data`, `redis-data`) persist
  data across `docker-compose down` (wipe with `down -v`). `model-data` is
  shared between the trainer (writes) and the API (reads).
- The `ml-32m/` dataset folder is mounted **read-only** into the containers
  that need it at `/data/ml-32m`.

### `db/init.sql`

Three tables — `users`, `movies`, `interactions` (one row per rating, with
foreign keys to the other two) — plus indexes on `interactions.user_id` and
`interactions.movie_id`, since "all ratings for user X / movie Y" are the
only queries the system makes. Postgres runs this file automatically on
first startup because it's mounted into `/docker-entrypoint-initdb.d/`.

---

## `kafka_producer/produce_events.py`

Replays the ratings CSV into the `user_events` topic to simulate live
traffic, sorted by timestamp so events arrive in chronological order.

Worth noticing in the code:

- **Message key = `user_id`** → all of a user's events go to the same
  partition → per-user ordering is preserved even with parallel consumers.
- **`--speed`**: `burst` (max throughput), `fast` (~10 ms pacing),
  `realtime` (replays the actual gaps between ratings, scaled 1000×).
- **`--limit N`** caps the number of events — useful for quick tests.
- **Backpressure**: `producer.produce()` raises `BufferError` when the
  local queue is full; the loop responds by polling (which drains delivery
  callbacks) and retrying, instead of crashing.
- **`wait_for_kafka()`** retries for 30 s at startup, because in Compose the
  producer container often boots before Kafka is ready.

## `stream_processor/app.py`

The real-time half of the system: a `confluent-kafka` consumer loop that
turns raw events into queryable user features. (The spec allowed Faust, but
a plain consumer loop shows the same concepts with far less magic.)

For every event it updates these Redis keys (24 h TTL on all of them):

| Redis key | Type | Meaning |
|-----------|------|---------|
| `user:{id}:recent_movies` | list (max 20) | Last 20 movie ids rated |
| `user:{id}:recent_ratings` | list (max 20) | Their ratings, parallel list |
| `user:{id}:avg_rating` | string | Running average rating |
| `user:{id}:rating_sum` | string | Internal: sum backing the average |
| `user:{id}:event_count` | string | Total events seen |
| `user:{id}:genre_counts` | hash | genre → count (e.g. `Drama: 15`) |
| `user:{id}:last_active` | string | Timestamp of latest event |

Implementation notes:

- All writes for one event go through a single **Redis pipeline** — one
  network round-trip instead of ~10, which is what makes >1000 events/sec
  possible.
- Genres come from an in-memory dict loaded once from `movies.csv` at
  startup — no per-event database lookups.
- Malformed events are logged and skipped, never crash the loop.
- SIGINT/SIGTERM set a flag; the loop exits cleanly and commits its Kafka
  offsets, so a restarted processor resumes where it left off.

## `recommendation_engine/`

### `train_model.py` — the batch job

Pipeline: load ratings (CSV or Postgres) → build a sparse user×movie matrix
(`scipy.sparse.csr_matrix`) → fit `TruncatedSVD(n_components=50)` → save
artifacts → precompute and cache top-20 recommendations in Redis for the
1000 most active users.

Artifacts written to `models/`:

| File | Contents |
|------|----------|
| `user_factors.npy` | matrix `(n_users, 50)` — each row is a user's taste vector |
| `item_factors.npy` | matrix `(n_items, 50)` — each row is a movie's profile vector |
| `user_map.json` | `user_id → row index` (matrix rows are positional, ids are not) |
| `item_map.json` | `movie_id → column index` |
| `movie_titles.json` | `movie_id → {title, genres}` for enriching responses |
| `model_metadata.json` | counts, component count, explained variance, training time |

If Redis is unreachable, training still succeeds — it just skips the
precompute step and logs a warning.

### `model.py` — the inference library

`RecommendationModel` loads those artifacts once (at API startup) and serves
from memory. Three public methods:

- **`get_recommendations(user_id, n)`** — the layered strategy: Redis cache
  → SVD dot-product scoring (excluding movies in
  `user:{id}:recent_movies`, then caching the result for 1 h) → genre-based
  fallback → globally-popular fallback. Never returns an error for an
  unknown user; degrades gracefully instead.
- **`get_similar_movies(movie_id, n)`** — cosine similarity between the
  query movie's factor vector and all others.
- **`get_popular_movies(n, genre)`** — ranks by item-factor norm (a cheap
  proxy for popularity: heavily-rated movies develop larger vectors),
  optionally filtered by genre.

If the model files don't exist yet (training still running), it initializes
empty with `loaded = False` and the API reports `"degraded"` on `/health`
instead of crashing. Every public method (and `/health`) calls
`ensure_loaded()`, which re-checks the disk at most once every 10 seconds —
so the API picks the model up automatically the moment training finishes,
and likewise after any later retraining into an empty dir. No restart
required.

### `utils.py`

Small pure helpers: `parse_genres` (splits `"Action|Sci-Fi"`),
`cosine_similarity_batch` (one vector vs. a whole matrix, vectorized, safe
against zero-norm vectors), `load_movie_metadata` (reads `movies.csv` into a
dict; imports pandas lazily so the API image doesn't need pandas).

## `api/`

### `main.py`

The FastAPI app. A **lifespan** handler runs at startup/shutdown: connect to
Redis, load the model into memory, create a Kafka producer — each one
optional, with the app degrading rather than dying if a dependency is down.

| Endpoint | What it does |
|----------|--------------|
| `GET /health` | Redis/model status + model metadata |
| `GET /recommendations?user_id&n` | Personalized recs; response includes `source` and `latency_ms` |
| `GET /similar?movie_id&n` | Similar movies via cosine similarity (404 if the movie is unknown) |
| `GET /popular?n&genre` | Popular movies, optionally by genre |
| `POST /events` | Validates a rating event and produces it to Kafka — the write path of the real-time loop |
| `GET /user/{id}/profile` | The live feature profile straight from Redis |
| `GET /docs` | Auto-generated Swagger UI |

An HTTP middleware stamps `X-Response-Time-Ms` on every response so you can
check the latency targets with plain `curl -i`.

### `schemas.py`

Pydantic models for every request/response. Validation is declarative —
`rating: float = Field(..., ge=0.5, le=5.0)` is what makes
`POST /events` with `rating: 6.0` return HTTP 422 automatically.

## `scripts/`

- **`seed_postgres.py`** — bulk-loads `movies.csv` and the first N ratings
  (default 1M) into Postgres using `psycopg2.extras.execute_values` batches;
  `ON CONFLICT DO NOTHING` makes it safe to re-run.
- **`seed_redis.py`** — optional warm-up that caches `movie:{id}:info` JSON
  blobs in Redis.
- **`init_kafka_topics.sh`** — creates the two topics; also embedded in the
  `kafka-init` compose service.

## `tests/`

32 tests, all runnable with **no services**: external dependencies are
replaced by small in-file fakes (`MockRedis`, `MockModel`, `MockProducer`,
`FakeRedis` + `FakePipeline`) rather than a mocking framework, so the test
files double as documentation of each interface.

| File | Covers |
|------|--------|
| `test_api.py` | Every endpoint via FastAPI's `TestClient`, including validation failures (422s), unknown-user fallback, and 404s |
| `test_model.py` | Model loading (present and missing artifacts), auto-reload when artifacts appear after startup, recommendation/similarity/popularity logic against a tiny fixture model, cosine-similarity math |
| `test_processor.py` | Feature extraction: all Redis keys written, 20-item list cap, genre counting, average-rating math, per-user isolation |
| `test_producer.py` | Event JSON serialization, key encoding, CSV loading, `--limit`, missing-file handling |

One subtlety in `test_api.py`: the fixture creates `TestClient(app)`
*without* a `with` block, so the app's lifespan handler never runs and the
mocked globals aren't overwritten by real connection attempts.
