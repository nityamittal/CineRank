"""Stream processor: consume user events from Kafka and extract features to Redis."""

import json
import logging
import os
import signal
import sys
import time

import pandas as pd
import redis
from confluent_kafka import Consumer, KafkaError, KafkaException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Graceful shutdown flag
shutdown_requested = False


def handle_signal(signum: int, frame: object) -> None:
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global shutdown_requested
    logger.info("Shutdown signal received, draining current messages...")
    shutdown_requested = True


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def load_movie_genres(movies_path: str) -> dict[int, list[str]]:
    """Load movie genres into a lookup dict.

    Args:
        movies_path: Path to movies.csv.

    Returns:
        Dict mapping movie_id to list of genre strings.
    """
    logger.info("Loading movie genres from %s", movies_path)
    df = pd.read_csv(movies_path)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"movieid": "movie_id"}, inplace=True)

    genres: dict[int, list[str]] = {}
    for _, row in df.iterrows():
        movie_id = int(row["movie_id"])
        genre_str = str(row.get("genres", ""))
        if genre_str and genre_str != "(no genres listed)":
            genres[movie_id] = genre_str.split("|")
        else:
            genres[movie_id] = []

    logger.info("Loaded genres for %d movies.", len(genres))
    return genres


def wait_for_redis(host: str, port: int, timeout: int = 30) -> redis.Redis:
    """Retry connecting to Redis until ready or timeout.

    Args:
        host: Redis host.
        port: Redis port.
        timeout: Max seconds to wait.

    Returns:
        A connected Redis client.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = redis.Redis(host=host, port=port, decode_responses=True)
            r.ping()
            logger.info("Connected to Redis at %s:%d", host, port)
            return r
        except redis.ConnectionError:
            logger.warning("Redis not ready, retrying in 2s...")
            time.sleep(2)

    raise ConnectionError(f"Could not connect to Redis within {timeout}s")


def wait_for_kafka(bootstrap_servers: str, group_id: str, timeout: int = 30) -> Consumer:
    """Retry connecting to Kafka until ready or timeout.

    Args:
        bootstrap_servers: Kafka bootstrap server address.
        group_id: Consumer group ID.
        timeout: Max seconds to wait.

    Returns:
        A connected Consumer instance.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            consumer = Consumer({
                "bootstrap.servers": bootstrap_servers,
                "group.id": group_id,
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,
                "auto.commit.interval.ms": 5000,
            })
            consumer.list_topics(timeout=5)
            logger.info("Connected to Kafka at %s", bootstrap_servers)
            return consumer
        except KafkaException as exc:
            logger.warning("Kafka not ready, retrying in 2s... (%s)", exc)
            time.sleep(2)

    raise ConnectionError(f"Could not connect to Kafka within {timeout}s")


def process_event(
    event: dict,
    redis_client: redis.Redis,
    movie_genres: dict[int, list[str]],
) -> None:
    """Process a single user event and update Redis features.

    Updates the following keys per user:
    - user:{id}:recent_movies — list of last 20 movie_ids
    - user:{id}:recent_ratings — list of last 20 ratings
    - user:{id}:avg_rating — running average rating
    - user:{id}:event_count — total interactions
    - user:{id}:genre_counts — hash of genre -> count
    - user:{id}:last_active — timestamp of most recent event

    Args:
        event: Parsed event dict with user_id, movie_id, rating, timestamp.
        redis_client: Redis connection.
        movie_genres: Movie genre lookup dict.
    """
    user_id = event["user_id"]
    movie_id = event["movie_id"]
    rating = event["rating"]
    timestamp = event["timestamp"]
    prefix = f"user:{user_id}"

    pipe = redis_client.pipeline()

    # 1. Push to recent lists (LPUSH + LTRIM to keep max 20)
    pipe.lpush(f"{prefix}:recent_movies", movie_id)
    pipe.ltrim(f"{prefix}:recent_movies", 0, 19)
    pipe.lpush(f"{prefix}:recent_ratings", rating)
    pipe.ltrim(f"{prefix}:recent_ratings", 0, 19)

    # 2. Increment event count
    pipe.incr(f"{prefix}:event_count")

    # 3. Track rating sum for running average
    pipe.incrbyfloat(f"{prefix}:rating_sum", rating)

    # 4. Update genre counts
    genres = movie_genres.get(movie_id, [])
    for genre in genres:
        pipe.hincrby(f"{prefix}:genre_counts", genre, 1)

    # 5. Update last active timestamp
    pipe.set(f"{prefix}:last_active", timestamp)

    # 6. Set TTL on all keys (24 hours)
    ttl = 86400
    for suffix in [
        "recent_movies", "recent_ratings", "avg_rating",
        "event_count", "rating_sum", "genre_counts", "last_active",
    ]:
        pipe.expire(f"{prefix}:{suffix}", ttl)

    pipe.execute()

    # 7. Compute running average (event_count and rating_sum are now updated)
    count_str = redis_client.get(f"{prefix}:event_count")
    sum_str = redis_client.get(f"{prefix}:rating_sum")
    if count_str and sum_str:
        count = int(count_str)
        rating_sum = float(sum_str)
        if count > 0:
            avg = round(rating_sum / count, 4)
            redis_client.set(f"{prefix}:avg_rating", avg, ex=ttl)


def run_consumer(
    consumer: Consumer,
    redis_client: redis.Redis,
    movie_genres: dict[int, list[str]],
    topic: str,
) -> None:
    """Main consumer loop: poll Kafka, process events, update Redis.

    Args:
        consumer: Kafka consumer.
        redis_client: Redis client.
        movie_genres: Movie genre lookup.
        topic: Kafka topic to consume from.
    """
    consumer.subscribe([topic])
    logger.info("Subscribed to topic '%s'. Waiting for messages...", topic)

    processed = 0
    errors = 0
    last_log_time = time.time()

    while not shutdown_requested:
        msg = consumer.poll(timeout=1.0)
        if msg is None:
            continue

        if msg.error():
            if msg.error().code() == KafkaError._PARTITION_EOF:
                continue
            logger.error("Consumer error: %s", msg.error())
            errors += 1
            continue

        try:
            event = json.loads(msg.value().decode("utf-8"))
            process_event(event, redis_client, movie_genres)
            processed += 1
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Skipping malformed event: %s", exc)
            errors += 1
            continue
        except redis.ConnectionError:
            logger.error("Redis connection lost, attempting reconnect...")
            try:
                redis_client.ping()
            except redis.ConnectionError:
                time.sleep(2)
            continue

        # Log throughput every 10 seconds
        now = time.time()
        if now - last_log_time >= 10:
            logger.info(
                "Processed %d events (%d errors) — %.0f events/sec",
                processed,
                errors,
                processed / (now - last_log_time) if last_log_time else 0,
            )
            last_log_time = now

    logger.info("Shutting down. Total processed: %d, errors: %d", processed, errors)
    consumer.close()


def main() -> None:
    kafka_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    topic = os.environ.get("KAFKA_TOPIC", "user_events")
    movies_path = os.environ.get("MOVIES_PATH", "/data/ml-32m/movies.csv")

    # Load movie genre lookup
    if os.path.exists(movies_path):
        movie_genres = load_movie_genres(movies_path)
    else:
        logger.warning("movies.csv not found at %s, genre tracking disabled.", movies_path)
        movie_genres = {}

    # Connect to services with retry
    redis_client = wait_for_redis(redis_host, redis_port)
    consumer = wait_for_kafka(kafka_servers, group_id="stream-processor")

    # Run main loop
    run_consumer(consumer, redis_client, movie_genres, topic)


if __name__ == "__main__":
    main()
