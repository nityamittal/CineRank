"""Pydantic request/response models for the CineRank API."""

from typing import List, Optional

from pydantic import BaseModel, Field


class UserEvent(BaseModel):
    """A user rating event to be produced to Kafka."""

    user_id: int = Field(..., gt=0)
    movie_id: int = Field(..., gt=0)
    rating: float = Field(..., ge=0.5, le=5.0)
    timestamp: Optional[int] = None


class MovieRecommendation(BaseModel):
    """A single movie recommendation."""

    movie_id: int
    title: str
    score: float
    genres: str


class RecommendationResponse(BaseModel):
    """Response for the /recommendations endpoint."""

    user_id: int
    recommendations: List[MovieRecommendation]
    source: str  # "cache", "computed", "popular_fallback"
    latency_ms: float


class SimilarMoviesResponse(BaseModel):
    """Response for the /similar endpoint."""

    movie_id: int
    similar: List[MovieRecommendation]
    latency_ms: float


class PopularMoviesResponse(BaseModel):
    """Response for the /popular endpoint."""

    movies: List[MovieRecommendation]
    genre: Optional[str] = None
    latency_ms: float


class UserProfile(BaseModel):
    """User feature profile from Redis."""

    user_id: int
    recent_movies: List[int]
    avg_rating: Optional[float]
    event_count: int
    top_genres: dict
    last_active: Optional[int]


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    redis_connected: bool
    model_loaded: bool
    model_info: dict


class EventResponse(BaseModel):
    """Response after submitting an event."""

    status: str
    message: str
