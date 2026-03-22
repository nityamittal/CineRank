"""Runtime inference module for generating recommendations."""

import json
import logging
import os

import numpy as np
import redis

from utils import cosine_similarity_batch, parse_genres

logger = logging.getLogger(__name__)


class RecommendationModel:
    """Loads trained SVD model and generates personalized recommendations.

    Attributes:
        user_factors: User latent factor matrix (n_users, n_components).
        item_factors: Item latent factor matrix (n_items, n_components).
        user_map: Mapping of user_id (int) to matrix row index.
        item_map: Mapping of movie_id (int) to matrix column index.
        idx_to_item: Reverse mapping of index to movie_id.
        movie_titles: Mapping of movie_id to {title, genres}.
        metadata: Model metadata dict.
        redis_client: Optional Redis client for caching.
    """

    def __init__(self, model_dir: str = "./models", redis_client: redis.Redis | None = None) -> None:
        """Load model artifacts from disk.

        Args:
            model_dir: Directory containing model files.
            redis_client: Optional Redis client for cache lookups.
        """
        self.model_dir = model_dir
        self.redis_client = redis_client
        self.loaded = False

        try:
            self.user_factors = np.load(os.path.join(model_dir, "user_factors.npy"))
            self.item_factors = np.load(os.path.join(model_dir, "item_factors.npy"))

            with open(os.path.join(model_dir, "user_map.json")) as f:
                raw_user_map = json.load(f)
                self.user_map: dict[int, int] = {int(k): v for k, v in raw_user_map.items()}

            with open(os.path.join(model_dir, "item_map.json")) as f:
                raw_item_map = json.load(f)
                self.item_map: dict[int, int] = {int(k): v for k, v in raw_item_map.items()}

            self.idx_to_item: dict[int, int] = {v: k for k, v in self.item_map.items()}

            with open(os.path.join(model_dir, "movie_titles.json")) as f:
                raw_titles = json.load(f)
                self.movie_titles: dict[int, dict[str, str]] = {
                    int(k): v for k, v in raw_titles.items()
                }

            with open(os.path.join(model_dir, "model_metadata.json")) as f:
                self.metadata: dict = json.load(f)

            self.loaded = True
            logger.info(
                "Model loaded: %d users, %d items, %d components",
                self.user_factors.shape[0],
                self.item_factors.shape[0],
                self.user_factors.shape[1],
            )
        except FileNotFoundError as exc:
            logger.warning("Model files not found in %s: %s", model_dir, exc)
            self._init_empty()

    def _init_empty(self) -> None:
        """Initialize empty model state for graceful degradation."""
        self.user_factors = np.array([])
        self.item_factors = np.array([])
        self.user_map = {}
        self.item_map = {}
        self.idx_to_item = {}
        self.movie_titles = {}
        self.metadata = {}

    def get_recommendations(
        self,
        user_id: int,
        n: int = 10,
        exclude_seen: bool = True,
    ) -> list[dict]:
        """Get top-N movie recommendations for a user.

        Strategy:
        1. Check Redis cache for precomputed recs.
        2. If cache miss and user exists in model: compute via dot product.
        3. If user not in model: fall back to popular movies.

        Args:
            user_id: User ID to get recommendations for.
            n: Number of recommendations to return.
            exclude_seen: Whether to exclude recently seen movies.

        Returns:
            List of dicts with movie_id, title, score, genres.
        """
        # 1. Check Redis cache
        if self.redis_client:
            try:
                cached = self.redis_client.get(f"recs:{user_id}")
                if cached:
                    recs = json.loads(cached)
                    return recs[:n]
            except (redis.ConnectionError, json.JSONDecodeError):
                pass

        # 2. Compute if user is in model
        if user_id in self.user_map:
            user_idx = self.user_map[user_id]
            scores = self.user_factors[user_idx] @ self.item_factors.T

            # Exclude recently seen movies
            seen_movies: set[int] = set()
            if exclude_seen and self.redis_client:
                try:
                    recent = self.redis_client.lrange(f"user:{user_id}:recent_movies", 0, -1)
                    seen_movies = {int(m) for m in recent}
                except redis.ConnectionError:
                    pass

            # Rank by score
            ranked_indices = np.argsort(scores)[::-1]
            recs = []
            for idx in ranked_indices:
                if len(recs) >= n:
                    break
                movie_id = self.idx_to_item.get(idx)
                if movie_id is None or movie_id in seen_movies:
                    continue
                meta = self.movie_titles.get(movie_id, {"title": "Unknown", "genres": ""})
                recs.append({
                    "movie_id": movie_id,
                    "title": meta["title"],
                    "score": round(float(scores[idx]), 4),
                    "genres": meta["genres"],
                })

            # Cache result
            if self.redis_client and recs:
                try:
                    self.redis_client.set(
                        f"recs:{user_id}",
                        json.dumps(recs),
                        ex=3600,
                    )
                except redis.ConnectionError:
                    pass

            return recs

        # 3. Cold start: check genre preferences from Redis
        if self.redis_client:
            try:
                genre_counts = self.redis_client.hgetall(f"user:{user_id}:genre_counts")
                if genre_counts:
                    top_genre = max(genre_counts, key=lambda g: int(genre_counts[g]))
                    return self.get_popular_movies(n=n, genre=top_genre)
            except redis.ConnectionError:
                pass

        # 4. Completely cold: return globally popular
        return self.get_popular_movies(n=n)

    def get_similar_movies(self, movie_id: int, n: int = 10) -> list[dict]:
        """Find N most similar movies using cosine similarity on item factors.

        Args:
            movie_id: Query movie ID.
            n: Number of similar movies to return.

        Returns:
            List of dicts with movie_id, title, score, genres.
        """
        if movie_id not in self.item_map:
            return []

        item_idx = self.item_map[movie_id]
        item_vector = self.item_factors[item_idx]

        similarities = cosine_similarity_batch(item_vector, self.item_factors)

        # Exclude the query movie itself
        similarities[item_idx] = -1.0

        top_indices = np.argsort(similarities)[::-1][:n]
        results = []
        for idx in top_indices:
            mid = self.idx_to_item.get(idx)
            if mid is None:
                continue
            meta = self.movie_titles.get(mid, {"title": "Unknown", "genres": ""})
            results.append({
                "movie_id": mid,
                "title": meta["title"],
                "score": round(float(similarities[idx]), 4),
                "genres": meta["genres"],
            })

        return results

    def get_popular_movies(self, n: int = 10, genre: str | None = None) -> list[dict]:
        """Return popular movies, optionally filtered by genre.

        Uses item factor norms as a proxy for popularity (items with stronger
        latent representations tend to be more rated).

        Args:
            n: Number of movies to return.
            genre: Optional genre filter.

        Returns:
            List of dicts with movie_id, title, score, genres.
        """
        if not self.loaded or self.item_factors.size == 0:
            return []

        norms = np.linalg.norm(self.item_factors, axis=1)
        ranked_indices = np.argsort(norms)[::-1]

        results = []
        for idx in ranked_indices:
            if len(results) >= n:
                break
            mid = self.idx_to_item.get(idx)
            if mid is None:
                continue
            meta = self.movie_titles.get(mid, {"title": "Unknown", "genres": ""})
            if genre and genre not in parse_genres(meta["genres"]):
                continue
            results.append({
                "movie_id": mid,
                "title": meta["title"],
                "score": round(float(norms[idx]), 4),
                "genres": meta["genres"],
            })

        return results
