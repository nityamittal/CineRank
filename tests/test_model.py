"""Tests for the recommendation engine."""

import json
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "recommendation_engine"))


@pytest.fixture
def model_dir():
    """Create a temporary model directory with test artifacts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        n_users, n_items, n_components = 10, 20, 5

        # Create random but deterministic factors
        rng = np.random.RandomState(42)
        user_factors = rng.randn(n_users, n_components).astype(np.float32)
        item_factors = rng.randn(n_items, n_components).astype(np.float32)

        np.save(os.path.join(tmpdir, "user_factors.npy"), user_factors)
        np.save(os.path.join(tmpdir, "item_factors.npy"), item_factors)

        user_map = {str(i + 1): i for i in range(n_users)}
        item_map = {str((i + 1) * 10): i for i in range(n_items)}

        with open(os.path.join(tmpdir, "user_map.json"), "w") as f:
            json.dump(user_map, f)
        with open(os.path.join(tmpdir, "item_map.json"), "w") as f:
            json.dump(item_map, f)

        movie_titles = {}
        genres_list = ["Action", "Comedy", "Drama", "Sci-Fi", "Thriller"]
        for i in range(n_items):
            mid = (i + 1) * 10
            movie_titles[str(mid)] = {
                "title": f"Test Movie {mid}",
                "genres": genres_list[i % len(genres_list)],
            }

        with open(os.path.join(tmpdir, "movie_titles.json"), "w") as f:
            json.dump(movie_titles, f)

        metadata = {
            "n_users": n_users,
            "n_items": n_items,
            "n_components": n_components,
            "explained_variance": 0.42,
            "trained_at": "2026-01-01T00:00:00",
        }
        with open(os.path.join(tmpdir, "model_metadata.json"), "w") as f:
            json.dump(metadata, f)

        yield tmpdir


def test_model_loads(model_dir: str) -> None:
    """RecommendationModel initializes without error when model files exist."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    assert m.loaded is True
    assert m.user_factors.shape == (10, 5)
    assert m.item_factors.shape == (20, 5)
    assert len(m.user_map) == 10
    assert len(m.item_map) == 20


def test_model_loads_missing_dir() -> None:
    """RecommendationModel degrades gracefully with missing files."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir="/nonexistent/path")
    assert m.loaded is False


def test_get_recommendations_known_user(model_dir: str) -> None:
    """get_recommendations returns a non-empty list for known users."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    recs = m.get_recommendations(user_id=1, n=5)
    assert len(recs) > 0
    assert len(recs) <= 5
    for rec in recs:
        assert "movie_id" in rec
        assert "title" in rec
        assert "score" in rec
        assert "genres" in rec


def test_get_recommendations_unknown_user(model_dir: str) -> None:
    """get_recommendations returns popular movies for unknown users."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    recs = m.get_recommendations(user_id=99999, n=5)
    # Should fall back to popular movies
    assert len(recs) > 0


def test_similar_movies(model_dir: str) -> None:
    """get_similar_movies returns results and excludes the query movie."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    similar = m.get_similar_movies(movie_id=10, n=5)
    assert len(similar) > 0
    assert len(similar) <= 5
    # Query movie should not be in results
    for s in similar:
        assert s["movie_id"] != 10


def test_similar_movies_unknown(model_dir: str) -> None:
    """get_similar_movies returns empty list for unknown movie."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    similar = m.get_similar_movies(movie_id=99999)
    assert similar == []


def test_popular_movies(model_dir: str) -> None:
    """get_popular_movies returns a list of movies."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    popular = m.get_popular_movies(n=5)
    assert len(popular) > 0
    assert len(popular) <= 5


def test_popular_movies_genre_filter(model_dir: str) -> None:
    """get_popular_movies with genre filter returns only matching genres."""
    from model import RecommendationModel
    m = RecommendationModel(model_dir=model_dir)
    popular = m.get_popular_movies(n=10, genre="Drama")
    for p in popular:
        assert "Drama" in p["genres"]


def test_cosine_similarity() -> None:
    """Verify cosine_similarity_batch with known vectors."""
    from utils import cosine_similarity_batch

    vec = np.array([1.0, 0.0, 0.0])
    matrix = np.array([
        [1.0, 0.0, 0.0],  # identical => 1.0
        [0.0, 1.0, 0.0],  # orthogonal => 0.0
        [-1.0, 0.0, 0.0],  # opposite => -1.0
        [1.0, 1.0, 0.0],  # 45 degrees => ~0.707
    ])

    sims = cosine_similarity_batch(vec, matrix)
    assert abs(sims[0] - 1.0) < 1e-6
    assert abs(sims[1] - 0.0) < 1e-6
    assert abs(sims[2] - (-1.0)) < 1e-6
    assert abs(sims[3] - 0.7071) < 1e-3


def test_cosine_similarity_zero_vector() -> None:
    """Verify cosine similarity with zero vector returns zeros."""
    from utils import cosine_similarity_batch

    vec = np.array([0.0, 0.0, 0.0])
    matrix = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    sims = cosine_similarity_batch(vec, matrix)
    assert np.all(sims == 0.0)
