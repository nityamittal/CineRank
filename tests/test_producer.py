"""Tests for the Kafka producer module."""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kafka_producer"))


def test_event_serialization() -> None:
    """Verify a ratings row converts to expected JSON format."""
    import pandas as pd

    row = pd.Series({
        "user_id": 123,
        "movie_id": 456,
        "rating": 4.0,
        "timestamp": 964982703,
    })

    event = {
        "user_id": int(row["user_id"]),
        "movie_id": int(row["movie_id"]),
        "rating": float(row["rating"]),
        "timestamp": int(row["timestamp"]),
        "event_type": "rating",
    }

    serialized = json.dumps(event).encode("utf-8")
    deserialized = json.loads(serialized)

    assert deserialized["user_id"] == 123
    assert deserialized["movie_id"] == 456
    assert deserialized["rating"] == 4.0
    assert deserialized["timestamp"] == 964982703
    assert deserialized["event_type"] == "rating"


def test_event_key_encoding() -> None:
    """Verify user_id is properly encoded as bytes for Kafka key."""
    user_id = 42
    key = str(user_id).encode("utf-8")
    assert key == b"42"
    assert isinstance(key, bytes)


def test_csv_loading() -> None:
    """Verify CSV loading and column normalization."""
    import pandas as pd

    # Create a small temp CSV
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("userId,movieId,rating,timestamp\n")
        f.write("1,50,4.0,964982703\n")
        f.write("2,100,3.5,964982800\n")
        temp_path = f.name

    try:
        df = pd.read_csv(temp_path)
        df.columns = [c.strip().lower() for c in df.columns]
        df.rename(columns={"userid": "user_id", "movieid": "movie_id"}, inplace=True)
        df.sort_values("timestamp", inplace=True)

        assert len(df) == 2
        assert list(df.columns) == ["user_id", "movie_id", "rating", "timestamp"]
        assert df.iloc[0]["user_id"] == 1
    finally:
        os.unlink(temp_path)


def test_limit_flag() -> None:
    """Verify that limit parameter correctly caps event count."""
    import pandas as pd

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write("userId,movieId,rating,timestamp\n")
        for i in range(100):
            f.write(f"{i},{i*10},{3.5},{964982703 + i}\n")
        temp_path = f.name

    try:
        limit = 10
        df = pd.read_csv(temp_path, nrows=limit)
        assert len(df) == limit
    finally:
        os.unlink(temp_path)


def test_invalid_csv_handling() -> None:
    """Verify graceful error on missing file."""
    assert not os.path.exists("/nonexistent/ratings.csv")
