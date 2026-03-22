"""Tests for the stream processor's feature extraction logic."""

import json
import os
import sys
from typing import Any

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stream_processor"))


class FakePipeline:
    """In-memory Redis pipeline stub."""

    def __init__(self, fake_redis: "FakeRedis") -> None:
        self.redis = fake_redis
        self.commands: list[tuple[str, tuple]] = []

    def lpush(self, key: str, value: Any) -> "FakePipeline":
        self.commands.append(("lpush", (key, value)))
        return self

    def ltrim(self, key: str, start: int, stop: int) -> "FakePipeline":
        self.commands.append(("ltrim", (key, start, stop)))
        return self

    def incr(self, key: str) -> "FakePipeline":
        self.commands.append(("incr", (key,)))
        return self

    def incrbyfloat(self, key: str, amount: float) -> "FakePipeline":
        self.commands.append(("incrbyfloat", (key, amount)))
        return self

    def hincrby(self, key: str, field: str, amount: int) -> "FakePipeline":
        self.commands.append(("hincrby", (key, field, amount)))
        return self

    def set(self, key: str, value: Any, ex: int | None = None) -> "FakePipeline":
        self.commands.append(("set", (key, value)))
        return self

    def expire(self, key: str, ttl: int) -> "FakePipeline":
        self.commands.append(("expire", (key, ttl)))
        return self

    def execute(self) -> list:
        results = []
        for cmd, args in self.commands:
            if cmd == "lpush":
                key, value = args
                if key not in self.redis.lists:
                    self.redis.lists[key] = []
                self.redis.lists[key].insert(0, str(value))
                results.append(len(self.redis.lists[key]))
            elif cmd == "ltrim":
                key, start, stop = args
                if key in self.redis.lists:
                    self.redis.lists[key] = self.redis.lists[key][start : stop + 1]
                results.append(True)
            elif cmd == "incr":
                key = args[0]
                val = int(self.redis.store.get(key, "0")) + 1
                self.redis.store[key] = str(val)
                results.append(val)
            elif cmd == "incrbyfloat":
                key, amount = args
                val = float(self.redis.store.get(key, "0")) + amount
                self.redis.store[key] = str(val)
                results.append(val)
            elif cmd == "hincrby":
                key, field, amount = args
                if key not in self.redis.hashes:
                    self.redis.hashes[key] = {}
                current = int(self.redis.hashes[key].get(field, "0"))
                self.redis.hashes[key][field] = str(current + amount)
                results.append(current + amount)
            elif cmd == "set":
                key, value = args
                self.redis.store[key] = str(value)
                results.append(True)
            elif cmd == "expire":
                results.append(True)
        self.commands = []
        return results


class FakeRedis:
    """In-memory Redis stub for testing."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def pipeline(self) -> FakePipeline:
        return FakePipeline(self)

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self.store[key] = str(value)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self.lists.get(key, [])
        return lst[start : stop + 1 if stop >= 0 else None]

    def hgetall(self, key: str) -> dict[str, str]:
        return self.hashes.get(key, {})


MOVIE_GENRES = {
    1: ["Adventure", "Animation", "Children", "Comedy", "Fantasy"],
    50: ["Crime", "Mystery", "Thriller"],
    110: ["Action", "Comedy", "Drama", "Thriller"],
    260: ["Action", "Adventure", "Drama", "Sci-Fi"],
    318: ["Crime", "Drama"],
}


def make_event(user_id: int, movie_id: int, rating: float, timestamp: int = 964982703) -> dict:
    """Create a test event dict."""
    return {
        "user_id": user_id,
        "movie_id": movie_id,
        "rating": rating,
        "timestamp": timestamp,
    }


def process_event_standalone(event: dict, redis_client: FakeRedis, movie_genres: dict) -> None:
    """Process a single event using the same logic as stream_processor/app.py."""
    from app import process_event
    process_event(event, redis_client, movie_genres)


def test_process_single_event() -> None:
    """Process one event, verify all Redis keys are set."""
    fake = FakeRedis()
    event = make_event(1, 318, 4.5, 964982703)
    process_event_standalone(event, fake, MOVIE_GENRES)

    # Recent movies
    assert "318" in fake.lists.get("user:1:recent_movies", [])
    # Event count
    assert fake.store.get("user:1:event_count") == "1"
    # Rating sum
    assert float(fake.store.get("user:1:rating_sum", "0")) == 4.5
    # Avg rating
    assert float(fake.store.get("user:1:avg_rating", "0")) == 4.5
    # Genre counts
    genres = fake.hashes.get("user:1:genre_counts", {})
    assert genres.get("Crime") == "1"
    assert genres.get("Drama") == "1"
    # Last active
    assert fake.store.get("user:1:last_active") == "964982703"


def test_recent_movies_max_20() -> None:
    """Process 25 events, verify list length stays at 20."""
    fake = FakeRedis()
    for i in range(25):
        event = make_event(1, i + 1, 3.0, 964982703 + i)
        process_event_standalone(event, fake, {})

    recent = fake.lists.get("user:1:recent_movies", [])
    assert len(recent) == 20


def test_genre_counts() -> None:
    """Process events for movies with known genres, verify counts."""
    fake = FakeRedis()
    # Movie 318 = Crime|Drama, Movie 50 = Crime|Mystery|Thriller
    process_event_standalone(make_event(1, 318, 4.0), fake, MOVIE_GENRES)
    process_event_standalone(make_event(1, 50, 3.5), fake, MOVIE_GENRES)

    genres = fake.hashes.get("user:1:genre_counts", {})
    assert genres.get("Crime") == "2"  # Both 318 and 50 are Crime
    assert genres.get("Drama") == "1"  # Only 318
    assert genres.get("Mystery") == "1"  # Only 50
    assert genres.get("Thriller") == "1"  # Only 50


def test_avg_rating_calculation() -> None:
    """Process 3 events with ratings 3, 4, 5 -> avg should be 4.0."""
    fake = FakeRedis()
    process_event_standalone(make_event(1, 1, 3.0), fake, MOVIE_GENRES)
    process_event_standalone(make_event(1, 50, 4.0), fake, MOVIE_GENRES)
    process_event_standalone(make_event(1, 318, 5.0), fake, MOVIE_GENRES)

    avg = float(fake.store.get("user:1:avg_rating", "0"))
    assert abs(avg - 4.0) < 0.01


def test_multiple_users() -> None:
    """Process events for different users, verify isolation."""
    fake = FakeRedis()
    process_event_standalone(make_event(1, 318, 5.0), fake, MOVIE_GENRES)
    process_event_standalone(make_event(2, 50, 3.0), fake, MOVIE_GENRES)

    assert fake.store.get("user:1:event_count") == "1"
    assert fake.store.get("user:2:event_count") == "1"
    assert float(fake.store.get("user:1:avg_rating", "0")) == 5.0
    assert float(fake.store.get("user:2:avg_rating", "0")) == 3.0
    assert "318" in fake.lists.get("user:1:recent_movies", [])
    assert "50" in fake.lists.get("user:2:recent_movies", [])
