"""Replay MovieLens ratings.csv as a stream of JSON events into Kafka."""

import argparse
import json
import logging
import os
import signal
import sys
import time

import pandas as pd
from confluent_kafka import KafkaError, KafkaException, Producer

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
    logger.info("Shutdown signal received, finishing current batch...")
    shutdown_requested = True


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


def delivery_callback(err: KafkaError | None, msg: object) -> None:
    """Log delivery failures."""
    if err is not None:
        logger.error("Message delivery failed: %s", err)


def wait_for_kafka(bootstrap_servers: str, timeout: int = 30) -> Producer:
    """Retry connecting to Kafka until ready or timeout.

    Args:
        bootstrap_servers: Kafka bootstrap server address.
        timeout: Max seconds to wait.

    Returns:
        A connected Producer instance.

    Raises:
        KafkaException: If connection cannot be established within timeout.
    """
    deadline = time.time() + timeout
    last_error: Exception | None = None

    while time.time() < deadline:
        try:
            producer = Producer({
                "bootstrap.servers": bootstrap_servers,
                "queue.buffering.max.messages": 100000,
                "queue.buffering.max.kbytes": 1048576,
                "batch.num.messages": 1000,
                "linger.ms": 10,
            })
            # Test connectivity by listing topics
            producer.list_topics(timeout=5)
            logger.info("Connected to Kafka at %s", bootstrap_servers)
            return producer
        except KafkaException as exc:
            last_error = exc
            logger.warning("Kafka not ready, retrying in 2s... (%s)", exc)
            time.sleep(2)

    raise KafkaException(f"Could not connect to Kafka within {timeout}s: {last_error}")


def produce_events(
    producer: Producer,
    data_path: str,
    topic: str,
    speed: str,
    limit: int,
) -> int:
    """Read ratings.csv and produce events to Kafka.

    Args:
        producer: Kafka producer instance.
        data_path: Path to ratings.csv.
        topic: Kafka topic name.
        speed: One of 'realtime', 'fast', 'burst'.
        limit: Max events to produce (0 = all).

    Returns:
        Number of events produced.
    """
    logger.info("Loading ratings from %s", data_path)
    nrows = limit if limit > 0 else None
    df = pd.read_csv(data_path, nrows=nrows)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"userid": "user_id", "movieid": "movie_id"}, inplace=True)

    # Sort by timestamp for chronological replay
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)

    total = len(df)
    logger.info("Producing %d events to topic '%s' (speed=%s)", total, topic, speed)

    produced = 0
    prev_ts: int | None = None

    for idx, row in df.iterrows():
        if shutdown_requested:
            logger.info("Shutdown requested, stopping after %d events.", produced)
            break

        event = {
            "user_id": int(row["user_id"]),
            "movie_id": int(row["movie_id"]),
            "rating": float(row["rating"]),
            "timestamp": int(row["timestamp"]),
            "event_type": "rating",
        }

        key = str(event["user_id"]).encode("utf-8")
        value = json.dumps(event).encode("utf-8")

        # Handle backpressure
        while True:
            try:
                producer.produce(
                    topic,
                    key=key,
                    value=value,
                    callback=delivery_callback,
                )
                break
            except BufferError:
                producer.poll(0.5)

        produced += 1
        producer.poll(0)

        # Speed control
        if speed == "fast":
            if produced % 100 == 0:
                time.sleep(0.01)
        elif speed == "realtime" and prev_ts is not None:
            gap = (int(row["timestamp"]) - prev_ts) / 1000.0
            if 0 < gap < 5:
                time.sleep(gap)

        prev_ts = int(row["timestamp"])

        # Log progress
        if produced % 10_000 == 0:
            logger.info(
                "Produced %d / %d events (%.2f%%)",
                produced,
                total,
                produced / total * 100,
            )

    producer.flush(timeout=30)
    logger.info("Done. Produced %d events total.", produced)
    return produced


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay MovieLens ratings into Kafka")
    parser.add_argument(
        "--data-path",
        default=os.environ.get("DATA_PATH", "/data/ml-32m/ratings.csv"),
        help="Path to ratings.csv",
    )
    parser.add_argument(
        "--kafka-broker",
        default=os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        help="Kafka bootstrap server",
    )
    parser.add_argument("--topic", default="user_events", help="Kafka topic name")
    parser.add_argument(
        "--speed",
        choices=["realtime", "fast", "burst"],
        default="fast",
        help="Replay speed: realtime, fast (10ms), burst (no delay)",
    )
    parser.add_argument("--limit", type=int, default=0, help="Max events to produce (0 = all)")
    args = parser.parse_args()

    if not os.path.exists(args.data_path):
        logger.error(
            "ratings.csv not found at %s. Make sure the ml-32m/ dataset folder is mounted or present.",
            args.data_path,
        )
        sys.exit(1)

    producer = wait_for_kafka(args.kafka_broker)
    produce_events(producer, args.data_path, args.topic, args.speed, args.limit)


if __name__ == "__main__":
    main()
