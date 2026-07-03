# Running CineRank Locally — Step by Step

This is a hands-on walkthrough from a fresh clone to a working
recommendation API, with the output you should expect at each step. New to
Kafka/Redis/SVD? Read [BEGINNER_GUIDE.md](BEGINNER_GUIDE.md) first.

There are two ways to run the project:

- **Path A — everything in Docker** (recommended): one command, no local
  Python setup beyond the dataset download.
- **Path B — hybrid**: infrastructure in Docker, Python services run
  directly on your machine. Better for development because you can edit
  code and restart instantly.

---

## 0. Prerequisites

- **Docker** and **Docker Compose** ([install guide](https://docs.docker.com/get-docker/))
- **Python 3.11+** (only needed for Path B and for running tests)
- ~2 GB free disk for the dataset

## 1. Get the dataset

Download MovieLens 32M from
[grouplens.org/datasets/movielens/32m](https://grouplens.org/datasets/movielens/32m/)
and unzip it so the files sit at the project root under `ml-32m/`:

```bash
curl -O https://files.grouplens.org/datasets/movielens/ml-32m.zip
unzip ml-32m.zip   # creates ml-32m/ with ratings.csv, movies.csv, tags.csv, links.csv
```

Check it:

```bash
head -3 ml-32m/ratings.csv
# userId,movieId,rating,timestamp
# 1,17,4.0,944249077
# 1,25,1.0,944250228
```

The folder is gitignored — the dataset never gets committed.

## 2. Environment variables

Copy the template (the defaults work out of the box for Docker):

```bash
cp .env.example .env
```

One subtlety worth understanding: **Kafka has two addresses**.
`kafka:29092` works *inside* the Docker network (containers reach each
other by service name), while `localhost:9092` works from your host
machine. That's why the file has both `KAFKA_BOOTSTRAP_SERVERS` and
`KAFKA_BOOTSTRAP_SERVERS_LOCAL`.

---

## Path A — everything in Docker

```bash
docker-compose up --build
```

What happens, in order (healthchecks enforce this ordering):

1. **zookeeper**, **kafka**, **redis**, **postgres** start and become healthy.
2. **kafka-init** creates the `user_events` and `processed_features` topics, then exits.
3. **recommendation-engine** trains the SVD model on the first 1M ratings
   (a few minutes), writes artifacts to the shared `model-data` volume,
   precomputes recommendations for the 1000 most active users into Redis,
   then exits. This container is a one-shot job, not a long-running service.
4. **producer** starts replaying 100,000 ratings into Kafka.
5. **processor** consumes them and writes user features to Redis. Watch its
   logs for lines like `Processed 40000 events (0 errors) — 3800 events/sec`.
6. **api** starts serving on port 8000. If it started before training
   finished, it logs a warning and serves in degraded mode — restart it
   after training completes (`docker-compose restart api`).

Then verify (see step 3 below).

---

## Path B — infrastructure in Docker, Python locally

```bash
# 1. Start only the infrastructure
docker-compose up -d zookeeper kafka redis postgres kafka-init

# 2. Install Python dependencies
pip install -r recommendation_engine/requirements.txt \
            -r api/requirements.txt \
            -r stream_processor/requirements.txt \
            -r kafka_producer/requirements.txt

# 3. Seed PostgreSQL with movies + the first 1M ratings (optional but recommended)
POSTGRES_HOST=localhost python scripts/seed_postgres.py \
    --ratings-path ./ml-32m/ratings.csv --movies-path ./ml-32m/movies.csv

# 4. Train the model (writes to ./models/)
REDIS_HOST=localhost python recommendation_engine/train_model.py \
    --csv-path ./ml-32m/ratings.csv --movies-path ./ml-32m/movies.csv \
    --max-ratings 1000000

# 5. Start the stream processor (terminal 2)
KAFKA_BOOTSTRAP_SERVERS=localhost:9092 REDIS_HOST=localhost \
    MOVIES_PATH=./ml-32m/movies.csv python stream_processor/app.py

# 6. Replay events into Kafka (terminal 3)
python kafka_producer/produce_events.py \
    --data-path ./ml-32m/ratings.csv --kafka-broker localhost:9092 \
    --speed fast --limit 100000

# 7. Start the API (terminal 4)
cd api && REDIS_HOST=localhost KAFKA_BOOTSTRAP_SERVERS=localhost:9092 \
    MODEL_DIR=../models uvicorn main:app --host 0.0.0.0 --port 8000
```

Expected training output:

```
[INFO] Loaded 1000000 ratings.
[INFO] Built sparse matrix: 6743 users x 22337 items, 1000000 non-zero entries.
[INFO] Training TruncatedSVD with 50 components...
[INFO] SVD training complete. Explained variance: 0.4xxx
[INFO] Saved artifacts to ./models: user_factors=(6743, 50), item_factors=(22337, 50)
[INFO] Precomputed recs for 1000 users.
```

---

## 3. Verify each layer

**API health:**

```bash
curl http://localhost:8000/health
# {"status":"ok","redis_connected":true,"model_loaded":true,"model_info":{...}}
```

**Recommendations** (user 1 exists in every MovieLens subset):

```bash
curl "http://localhost:8000/recommendations?user_id=1&n=5"
```

Note the `source` field: `"cache"` (precomputed), `"computed"` (scored on
the fly), or `"popular_fallback"` (cold-start user). Try a huge user id like
`user_id=99999999` to see the fallback in action.

**Real-time features in Redis:**

```bash
docker exec -it cinerank-redis redis-cli
> GET user:1:avg_rating
> LRANGE user:1:recent_movies 0 -1
> HGETALL user:1:genre_counts
```

**Messages actually flowing through Kafka:**

```bash
docker exec -it cinerank-kafka kafka-console-consumer \
  --bootstrap-server localhost:9092 --topic user_events \
  --from-beginning --max-messages 3
```

**Postgres seeded:**

```bash
docker exec -it cinerank-postgres psql -U recuser -d recdb \
  -c "SELECT COUNT(*) FROM interactions;"
```

**The full real-time loop** — submit an event via the API and watch it come
back out in the user's profile a moment later:

```bash
curl -X POST http://localhost:8000/events \
  -H "Content-Type: application/json" \
  -d '{"user_id": 1, "movie_id": 2571, "rating": 5.0}'

curl http://localhost:8000/user/1/profile   # 2571 appears in recent_movies
```

**Interactive API docs:** open <http://localhost:8000/docs> — FastAPI
generates a Swagger UI where you can call every endpoint from the browser.

## 4. Run the tests

The test suite mocks Kafka, Redis, and the model, so it needs **no running
services**:

```bash
pip install -r requirements-test.txt -r api/requirements.txt \
            -r recommendation_engine/requirements.txt
pytest tests/ -v
# ======================== 31 passed ========================
```

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|-------------------|
| Producer logs `ratings.csv not found` | The `ml-32m/` folder isn't at the project root (Docker mounts it read-only at `/data/ml-32m`) |
| API `/health` says `"degraded"`, `model_loaded: false` | Training hasn't finished yet, or the API can't see the model dir. Docker: wait for `cinerank-rec-engine` to exit, then `docker-compose restart api`. Local: check `MODEL_DIR` points at the folder containing `user_factors.npy` |
| `Kafka not ready, retrying in 2s...` forever | Kafka can take ~30 s on first boot. If it persists, check `docker-compose logs kafka` — often a stale volume; `docker-compose down -v` resets everything |
| `/user/{id}/profile` returns 404 | The stream processor hasn't processed any events for that user yet — is the producer running? Did you pick a user id that appears in the replayed slice? |
| Port already in use (8000/9092/6379/5432) | Another local service owns the port — stop it or change the port mapping in `docker-compose.yml` |
| Training is slow / out of memory | Lower `--max-ratings` (e.g. `500000`). Full 32M needs ~8 GB RAM |
| `docker-compose down` then weird Postgres state | Data persists in named volumes. `docker-compose down -v` wipes them for a truly fresh start |
