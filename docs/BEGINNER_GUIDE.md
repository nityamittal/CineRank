# CineRank for Beginners — How It All Works

This guide explains CineRank from the ground up. It assumes you know basic
Python but have never used Kafka, Redis, or built a recommendation system.
Read this first, then [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md) to run it
yourself, then [COMPONENTS.md](COMPONENTS.md) for a file-by-file tour of the
code.

---

## 1. What problem does CineRank solve?

Imagine you run a movie streaming site. Users rate movies all day long, and
you want to show each user a personalized "Recommended for you" row that:

1. **Reflects their taste** — someone who loves sci-fi shouldn't be shown
   romantic comedies.
2. **Updates in near real time** — if a user just rated five horror movies,
   the system should know about it within a second, not after tonight's
   batch job.
3. **Responds fast** — the recommendation API must answer in milliseconds,
   because it sits on the critical path of rendering your homepage.

CineRank is a small but realistic version of exactly that system, built
entirely with free, open-source tools and trained on the public
[MovieLens 32M](https://grouplens.org/datasets/movielens/32m/) dataset
(32 million real movie ratings).

---

## 2. The big picture

```
ratings.csv → Kafka Producer → Kafka (user_events topic)
    → Stream Processor → Redis (user features + cached recommendations)
    → Recommendation Engine (SVD model)
    → FastAPI → Client
```

Two separate "speeds" of work happen at once, and this split is the single
most important idea in the whole project:

| Path | Speed | What it does |
|------|-------|--------------|
| **Streaming path** (Kafka → processor → Redis) | milliseconds | Keeps a live profile of every user: what they watched recently, their favorite genres, their average rating |
| **Batch path** (training script) | minutes, run occasionally | Learns deep taste patterns from millions of historical ratings and saves them as a model |

The API then combines both: the batch model provides "what does this user
like in general," and the streaming features provide "what have they done in
the last hour." Real production systems at Netflix or Spotify follow the
same shape — this is often called a **lambda architecture**.

---

## 3. The cast of characters (glossary)

### Apache Kafka — the conveyor belt

Kafka is a **message broker**: programs write messages onto named channels
(called **topics**), and other programs read them off, in order. Think of it
as a durable, super-fast conveyor belt between services.

- A **producer** puts messages on the belt (our `kafka_producer/` replays
  the ratings file as if users were rating movies live).
- A **consumer** takes messages off the belt (our `stream_processor/`).
- A **topic** is a named belt. Ours is called `user_events`.
- A topic is split into **partitions** (ours has 3), which allows several
  consumers to work in parallel. Messages are assigned to a partition by
  their **key** — we key by `user_id`, which guarantees all events for the
  same user land on the same partition and are processed in order.

Why not just call the processor directly? Because Kafka **decouples**
services: the producer doesn't need to know who consumes the events, a slow
consumer doesn't slow down the producer, and if a consumer crashes, the
messages wait on the belt until it comes back.

### Redis — the sticky notes

Redis is an **in-memory key-value store**: a giant, extremely fast Python
dict that lives in its own process. Reads and writes take well under a
millisecond, which is why we use it for anything the API needs to look up
per-request.

CineRank uses Redis for two jobs:

1. **Feature store** — the stream processor writes live per-user stats here
   (see the key table in [COMPONENTS.md](COMPONENTS.md)).
2. **Recommendation cache** — computed recommendation lists are stored under
   `recs:{user_id}` so repeat requests are answered without touching the
   model at all.

Keys are written with a **TTL** (time-to-live), so stale data expires
automatically instead of accumulating forever.

### PostgreSQL — the filing cabinet

Postgres is a classic relational database. It holds the durable, historical
copy of the data: the `movies` catalog, `users`, and every rating in an
`interactions` table. Redis can lose everything on restart and be rebuilt;
Postgres is the source of truth.

### FastAPI — the front desk

FastAPI is a modern Python web framework. It exposes the HTTP endpoints
(`/recommendations`, `/similar`, `/events`, …), validates request and
response shapes using **Pydantic** models (`api/schemas.py`), and
auto-generates interactive documentation at `/docs`.

### The model — SVD collaborative filtering

**Collaborative filtering** is the idea that people who rated things
similarly in the past will like similar things in the future. No plot
summaries, no actor lists — just the pattern of who rated what.

Concretely:

1. Build a giant table (matrix) with one row per user and one column per
   movie; cell `(u, m)` holds user *u*'s rating of movie *m*. With 200K
   users × 87K movies it's almost entirely empty, so we store it as a
   **sparse matrix** (only the ~32M filled cells).
2. Run **Truncated SVD** (singular value decomposition, from scikit-learn)
   to compress that huge matrix into two skinny ones:
   - **user factors**: one row of 50 numbers per user
   - **item factors**: one row of 50 numbers per movie
3. Those 50 numbers are **latent factors** — dimensions of taste the math
   discovered on its own (in practice they end up loosely tracking things
   like "likes blockbusters," "likes 90s indie drama," …).
4. To predict how much user *u* would like movie *m*, take the **dot
   product** of their two vectors. Higher score = better match. To build a
   recommendation list, score every movie and take the top N.
5. "Movies similar to X" uses **cosine similarity** between item vectors —
   movies whose taste-vectors point the same direction.

Training takes minutes on a laptop and needs no GPU, which is exactly why
this project uses SVD instead of a neural network.

### The cold-start problem

What about a brand-new user the model has never seen? This is the
**cold-start problem**, and CineRank handles it in layers
(`recommendation_engine/model.py`):

1. User in the model → personalized SVD recommendations.
2. User not in the model, but the stream processor has seen them → look up
   their live genre counts in Redis and return popular movies in their
   favorite genre.
3. Complete stranger → return globally popular movies.

The API reports which layer answered in the `source` field of every
response (`"cache"`, `"computed"`, or `"popular_fallback"`).

### Docker Compose — the stage manager

Each service (Kafka, Redis, Postgres, our four Python apps) runs in its own
**container** — a lightweight, isolated box with exactly the dependencies it
needs. `docker-compose.yml` describes the whole cast and their startup
order (with healthchecks, so the producer doesn't start before Kafka is
actually ready). One command — `docker-compose up` — starts everything.

---

## 4. Life of a rating: following one event through the system

The best way to understand the architecture is to trace a single event.
Suppose user **42** rates *The Matrix* (movie 2571) five stars.

**Step 1 — the event enters Kafka.**
A JSON message is produced onto the `user_events` topic, keyed by user id:

```json
{"user_id": 42, "movie_id": 2571, "rating": 5.0, "timestamp": 1710000000, "event_type": "rating"}
```

In the demo this comes from `kafka_producer/produce_events.py` replaying
history, but it could equally come from the API's `POST /events` endpoint —
both write to the same topic, and downstream code cannot tell the
difference. That is the point of decoupling.

**Step 2 — the stream processor updates the user's live profile.**
`stream_processor/app.py` is sitting in a poll loop. Within milliseconds it
pulls the message, looks up The Matrix's genres from an in-memory dict
(loaded once at startup from `movies.csv`), and updates a batch of Redis
keys in one atomic **pipeline** (one network round-trip instead of eight):

- pushes `2571` onto `user:42:recent_movies` (trimmed to the last 20)
- increments `user:42:event_count` and `user:42:rating_sum`
- increments `Action` and `Sci-Fi` in the `user:42:genre_counts` hash
- recomputes `user:42:avg_rating`
- refreshes each key's 24-hour TTL

**Step 3 — the profile is instantly visible.**
`GET /user/42/profile` now reflects the new rating — under a second after
the event was produced. This is the "real-time" in the project title.

**Step 4 — recommendations are served.**
When `GET /recommendations?user_id=42` arrives, the API:

1. checks Redis for `recs:42` — if a cached list exists, return it
   (~1–5 ms, `source: "cache"`);
2. otherwise looks user 42 up in the trained model, dot-products their
   50-number taste vector against all 87K movie vectors, **filters out the
   movies in `user:42:recent_movies`** (no point recommending what they
   just watched — this is where the streaming and batch paths meet), takes
   the top N, enriches with titles/genres, caches the result for an hour,
   and returns it (`source: "computed"`);
3. if user 42 weren't in the model at all, it would fall through the
   cold-start layers described above.

**Step 5 — the model catches up later.**
User 42's five-star rating doesn't change their model vector until the next
time `train_model.py` runs. Between trainings, the streaming features are
what keep the system feeling fresh. This lag is a deliberate trade-off —
retraining continuously would be expensive; the cache TTLs (1 h for recs,
24 h for features) bound how stale anything can get.

---

## 5. Why these tools? (design decisions)

| Decision | Why |
|----------|-----|
| **Kafka** instead of calling services directly | Decouples producers from consumers; buffers bursts; lets consumers scale horizontally by partition; events can be replayed |
| **A plain Kafka consumer loop** instead of Faust/Flink | Same streaming concepts, pure Python, one less framework to debug. The consumer loop in `stream_processor/app.py` is ~50 lines you can read top to bottom |
| **Redis** for features *and* cache | Sub-millisecond reads on the API's hot path; TTLs give free garbage collection. In a bigger system you might split these two roles |
| **TruncatedSVD** instead of deep learning | Trains in minutes on a laptop, no GPU, well-understood, and genuinely competitive on MovieLens-style data |
| **PostgreSQL** for history | Durable source of truth; lets you rebuild Redis or retrain the model at any time |
| **Docker Compose** | The entire seven-service system starts with one command, identically on any machine |

---

## 6. Where to go next

- **Run it**: [RUNNING_LOCALLY.md](RUNNING_LOCALLY.md) walks through setup
  command by command, including what output to expect.
- **Read the code**: [COMPONENTS.md](COMPONENTS.md) tours every file and
  documents the Redis key schema and model artifacts.
- **Extend it**: the README lists ideas (A/B testing, Grafana monitoring,
  neural embeddings). A good first exercise: add a `GET /genres` endpoint
  that lists all genres with movie counts — it touches the API, the model
  metadata, and the tests, but nothing else.
