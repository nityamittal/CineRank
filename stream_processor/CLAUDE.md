# CLAUDE.md — CineRank: Stream Processor

## Goal

Consume events from Kafka `user_events` topic in real time, extract per-user features, and store them in Redis for low-latency access by the API.

## Implementation: `stream_processor/app.py`

### Approach

Use a simple `confluent-kafka` Consumer in a loop. Faust is an option but adds complexity — a well-structured consumer loop demonstrates the same streaming concepts and is easier to debug.

### Features Computed Per User

For each incoming event, update these Redis keys:

| Redis Key | Type | Description |
|-----------|------|-------------|
| `user:{id}:recent_movies` | List (max 20) | Last 20 movie_ids the user rated |
| `user:{id}:recent_ratings` | List (max 20) | Last 20 ratings (parallel to recent_movies) |
| `user:{id}:avg_rating` | String (float) | Running average rating |
| `user:{id}:event_count` | String (int) | Total number of events processed |
| `user:{id}:genre_counts` | Hash | genre → count mapping |
| `user:{id}:last_active` | String (int) | Timestamp of most recent event |

### Processing Logic

```python
def process_event(event: dict, redis_client, movie_genres: dict):
    user_id = event["user_id"]
    movie_id = event["movie_id"]
    rating = event["rating"]
    timestamp = event["timestamp"]
    
    pipe = redis_client.pipeline()
    
    # 1. Push to recent lists (LPUSH + LTRIM to keep max 20)
    pipe.lpush(f"user:{user_id}:recent_movies", movie_id)
    pipe.ltrim(f"user:{user_id}:recent_movies", 0, 19)
    pipe.lpush(f"user:{user_id}:recent_ratings", rating)
    pipe.ltrim(f"user:{user_id}:recent_ratings", 0, 19)
    
    # 2. Increment event count
    pipe.incr(f"user:{user_id}:event_count")
    
    # 3. Update genre counts
    genres = movie_genres.get(movie_id, [])
    for genre in genres:
        pipe.hincrby(f"user:{user_id}:genre_counts", genre, 1)
    
    # 4. Update last active timestamp
    pipe.set(f"user:{user_id}:last_active", timestamp)
    
    # 5. Set TTL on all keys (24 hours)
    for key_suffix in ["recent_movies", "recent_ratings", "avg_rating", 
                        "event_count", "genre_counts", "last_active"]:
        pipe.expire(f"user:{user_id}:{key_suffix}", 86400)
    
    pipe.execute()
    
    # 6. Compute running average (needs current count and sum)
    # Use a separate INCRBYFLOAT for sum, then divide
```

### Movie Genre Lookup

On startup, load movie genres into memory:
- Option A: Query PostgreSQL `movies` table
- Option B: Read `movies.csv` directly from mounted data volume
- Store as `dict[int, list[str]]`: `{1: ["Adventure", "Animation", "Children"], ...}`

### Consumer Configuration

```python
consumer_config = {
    "bootstrap.servers": os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
    "group.id": "stream-processor",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": True,
    "auto.commit.interval.ms": 5000,
}
```

### Batch Processing for Throughput

- Poll Kafka in a loop with `consumer.poll(timeout=1.0)`
- Process events individually but use Redis pipelines (batch writes)
- Optionally batch: accumulate N events, then flush pipeline
- Log throughput every 10 seconds: `"Processed 50,000 events (2,500/sec)"`

### Graceful Shutdown

- Handle SIGINT / SIGTERM
- Commit offsets: `consumer.close()` triggers a final commit
- Close Redis connection

### Error Handling

- Retry Redis connection on startup (wait for Redis to be healthy)
- Retry Kafka connection on startup
- On individual event processing error: log and skip (don't crash the consumer)
- On Redis connection loss mid-stream: attempt reconnect with backoff

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "app.py"]
```

### requirements.txt

```
confluent-kafka>=2.3.0
redis>=5.0.0
psycopg2-binary>=2.9.0
pandas>=2.0.0
```

### Verification

```bash
# After processor has been running while producer sends events:
docker exec -it $(docker-compose ps -q redis) redis-cli

# Check a user's features
> LRANGE user:1:recent_movies 0 -1
> GET user:1:avg_rating
> HGETALL user:1:genre_counts
> GET user:1:event_count
```

All keys should have values if user 1 has events in the dataset.
