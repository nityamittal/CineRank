"""FastAPI application for serving CineRank recommendations."""

import json
import logging
import os
import sys
import time

import numpy as np
import redis
from confluent_kafka import Producer, KafkaException
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# Add recommendation_engine to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recommendation_engine"))
from model import RecommendationModel

from schemas import (
    EventResponse,
    HealthResponse,
    MovieRecommendation,
    PopularMoviesResponse,
    RecommendationResponse,
    SimilarMoviesResponse,
    UserEvent,
    UserProfile,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CineRank",
    description="Real-Time Personalized Movie Recommendation API",
    version="1.0.0",
)

# CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
redis_client: redis.Redis | None = None
kafka_producer: Producer | None = None
rec_model: RecommendationModel | None = None


@app.middleware("http")
async def add_timing_header(request, call_next):
    """Add X-Response-Time-Ms header to every response."""
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000
    response.headers["X-Response-Time-Ms"] = f"{duration:.2f}"
    return response


@app.on_event("startup")
async def startup() -> None:
    """Initialize connections to Redis, Kafka, and load the model."""
    global redis_client, kafka_producer, rec_model

    # Connect to Redis
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    try:
        redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        redis_client.ping()
        logger.info("Connected to Redis at %s:%d", redis_host, redis_port)
    except redis.ConnectionError as exc:
        logger.warning("Redis not available: %s. Continuing without cache.", exc)
        redis_client = None

    # Load recommendation model
    model_dir = os.environ.get("MODEL_DIR", "./models")
    rec_model = RecommendationModel(model_dir=model_dir, redis_client=redis_client)

    # Create Kafka producer
    kafka_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    try:
        kafka_producer = Producer({"bootstrap.servers": kafka_servers})
        kafka_producer.list_topics(timeout=5)
        logger.info("Connected to Kafka at %s", kafka_servers)
    except KafkaException as exc:
        logger.warning("Kafka not available: %s. Event ingestion disabled.", exc)
        kafka_producer = None

    logger.info("CineRank API startup complete.")


@app.on_event("shutdown")
async def shutdown() -> None:
    """Flush Kafka producer and close Redis."""
    if kafka_producer:
        kafka_producer.flush(timeout=5)
    if redis_client:
        redis_client.close()
    logger.info("CineRank API shutdown complete.")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Health check — verifies Redis and model are loaded."""
    redis_ok = False
    if redis_client:
        try:
            redis_client.ping()
            redis_ok = True
        except redis.ConnectionError:
            pass

    return HealthResponse(
        status="ok" if redis_ok and rec_model and rec_model.loaded else "degraded",
        redis_connected=redis_ok,
        model_loaded=rec_model.loaded if rec_model else False,
        model_info=rec_model.metadata if rec_model else {},
    )


@app.get("/recommendations", response_model=RecommendationResponse)
async def get_recommendations(
    user_id: int = Query(..., gt=0, description="User ID"),
    n: int = Query(10, ge=1, le=100, description="Number of recommendations"),
) -> RecommendationResponse:
    """Get personalized movie recommendations for a user."""
    if not rec_model:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()

    # Check if result will come from cache
    cached = False
    if redis_client:
        try:
            cached = redis_client.exists(f"recs:{user_id}") > 0
        except redis.ConnectionError:
            pass

    recs = rec_model.get_recommendations(user_id, n=n)
    latency_ms = (time.perf_counter() - start) * 1000

    # Determine source
    if cached:
        source = "cache"
    elif user_id in rec_model.user_map:
        source = "computed"
    else:
        source = "popular_fallback"

    return RecommendationResponse(
        user_id=user_id,
        recommendations=[MovieRecommendation(**r) for r in recs],
        source=source,
        latency_ms=round(latency_ms, 2),
    )


@app.get("/similar", response_model=SimilarMoviesResponse)
async def get_similar(
    movie_id: int = Query(..., gt=0, description="Movie ID"),
    n: int = Query(10, ge=1, le=100, description="Number of similar movies"),
) -> SimilarMoviesResponse:
    """Get movies similar to a given movie."""
    if not rec_model:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    similar = rec_model.get_similar_movies(movie_id, n=n)
    latency_ms = (time.perf_counter() - start) * 1000

    if not similar:
        raise HTTPException(status_code=404, detail=f"Movie {movie_id} not found in model")

    return SimilarMoviesResponse(
        movie_id=movie_id,
        similar=[MovieRecommendation(**s) for s in similar],
        latency_ms=round(latency_ms, 2),
    )


@app.get("/popular", response_model=PopularMoviesResponse)
async def get_popular(
    n: int = Query(10, ge=1, le=100, description="Number of movies"),
    genre: str | None = Query(None, description="Optional genre filter"),
) -> PopularMoviesResponse:
    """Get globally popular movies, optionally filtered by genre."""
    if not rec_model:
        raise HTTPException(status_code=503, detail="Model not loaded")

    start = time.perf_counter()
    movies = rec_model.get_popular_movies(n=n, genre=genre)
    latency_ms = (time.perf_counter() - start) * 1000

    return PopularMoviesResponse(
        movies=[MovieRecommendation(**m) for m in movies],
        genre=genre,
        latency_ms=round(latency_ms, 2),
    )


@app.post("/events", response_model=EventResponse)
async def post_event(event: UserEvent) -> EventResponse:
    """Submit a new user event to Kafka."""
    if not kafka_producer:
        raise HTTPException(status_code=503, detail="Kafka producer not available")

    # Auto-set timestamp if missing
    if event.timestamp is None:
        event.timestamp = int(time.time())

    payload = {
        "user_id": event.user_id,
        "movie_id": event.movie_id,
        "rating": event.rating,
        "timestamp": event.timestamp,
        "event_type": "rating",
    }

    try:
        kafka_producer.produce(
            "user_events",
            key=str(event.user_id).encode("utf-8"),
            value=json.dumps(payload).encode("utf-8"),
        )
        kafka_producer.poll(0)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to produce event: {exc}")

    return EventResponse(
        status="ok",
        message=f"Event for user {event.user_id} on movie {event.movie_id} queued.",
    )


@app.get("/user/{user_id}/profile", response_model=UserProfile)
async def get_user_profile(user_id: int) -> UserProfile:
    """Get a user's real-time feature profile from Redis."""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")

    prefix = f"user:{user_id}"

    try:
        # Check if user has any data
        event_count_str = redis_client.get(f"{prefix}:event_count")
        if not event_count_str:
            raise HTTPException(status_code=404, detail=f"No profile data for user {user_id}")

        recent_movies_raw = redis_client.lrange(f"{prefix}:recent_movies", 0, -1)
        recent_movies = [int(m) for m in recent_movies_raw]

        avg_rating_str = redis_client.get(f"{prefix}:avg_rating")
        avg_rating = float(avg_rating_str) if avg_rating_str else None

        event_count = int(event_count_str)

        genre_counts = redis_client.hgetall(f"{prefix}:genre_counts")
        top_genres = {k: int(v) for k, v in genre_counts.items()}

        last_active_str = redis_client.get(f"{prefix}:last_active")
        last_active = int(last_active_str) if last_active_str else None

    except redis.ConnectionError as exc:
        raise HTTPException(status_code=503, detail=f"Redis error: {exc}")

    return UserProfile(
        user_id=user_id,
        recent_movies=recent_movies,
        avg_rating=avg_rating,
        event_count=event_count,
        top_genres=top_genres,
        last_active=last_active,
    )
