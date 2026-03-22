# CLAUDE.md — CineRank: Kafka Producer (Event Replay)

## Goal

Replay the MovieLens `ratings.csv` as a stream of JSON events into Kafka's `user_events` topic, simulating real-time user activity.

## Implementation: `kafka_producer/produce_events.py`

### Core Logic

1. Load `ratings.csv` with pandas
2. Sort by `timestamp` column (ascending) — this simulates chronological order
3. For each row, serialize to JSON and produce to Kafka topic `user_events`
4. Key the message by `user_id` (ensures all events for a user go to the same partition)

### Event Schema

```json
{
  "user_id": 123,
  "movie_id": 456,
  "rating": 4.0,
  "timestamp": 964982703,
  "event_type": "rating"
}
```

### CLI Arguments

```
--data-path     Path to ratings.csv (default: /data/ml-32m/ratings.csv)
--kafka-broker  Kafka bootstrap server (default: from env KAFKA_BOOTSTRAP_SERVERS or localhost:9092)
--topic         Kafka topic name (default: user_events)
--speed         One of: realtime | fast | burst
                  realtime = use actual timestamp gaps (scaled down 1000x)
                  fast = 10ms between events
                  burst = no delay, maximum throughput
--limit         Max number of events to send (default: 0 = all)
--batch-size    Produce in batches for throughput (default: 1000)
```

### Key Implementation Details

- Use `confluent-kafka` Producer (not `kafka-python` — confluent is faster and better maintained)
- Use `producer.produce()` with a delivery callback to track success/failure
- Call `producer.poll(0)` periodically and `producer.flush()` at the end
- Message key = `str(user_id).encode('utf-8')`
- Message value = JSON bytes
- Log progress every 10,000 events: `"Produced 10000/25000095 events (0.04%)"`
- Handle `BufferError` by polling and retrying
- Graceful shutdown on SIGINT

### Error Handling

- Retry connection to Kafka up to 30 seconds (other containers may still be starting)
- If `ratings.csv` not found, print helpful message saying the ml-32m/ dataset folder must be in the project root

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "produce_events.py", "--speed", "fast", "--limit", "100000"]
```

### requirements.txt

```
confluent-kafka>=2.3.0
pandas>=2.0.0
```

### Testing

- Unit test: verify JSON serialization of a sample row
- Integration test: produce 10 events, consume them back, verify content matches
- Run standalone: `python produce_events.py --speed burst --limit 1000`

### Verification

```bash
# After producer runs, check topic has messages:
docker exec -it $(docker-compose ps -q kafka) kafka-console-consumer \
  --bootstrap-server localhost:9092 \
  --topic user_events \
  --from-beginning \
  --max-messages 5
```

Should output 5 JSON objects with user_id, movie_id, rating, timestamp fields.
