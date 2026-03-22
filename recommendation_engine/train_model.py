"""Batch training script: build user-item matrix, compute SVD, save artifacts."""

import argparse
import json
import logging
import os
import sys
import time

import numpy as np
import pandas as pd
import redis
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

from utils import load_movie_metadata

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def load_ratings_csv(csv_path: str, max_ratings: int = 0) -> pd.DataFrame:
    """Load ratings from CSV file.

    Args:
        csv_path: Path to ratings.csv.
        max_ratings: Max rows to load (0 = all).

    Returns:
        DataFrame with columns: user_id, movie_id, rating, timestamp.
    """
    logger.info("Loading ratings from %s (limit=%s)", csv_path, max_ratings or "all")
    nrows = max_ratings if max_ratings > 0 else None
    df = pd.read_csv(csv_path, nrows=nrows)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"userid": "user_id", "movieid": "movie_id"}, inplace=True)
    logger.info("Loaded %d ratings.", len(df))
    return df


def load_ratings_postgres() -> pd.DataFrame:
    """Load ratings from PostgreSQL.

    Returns:
        DataFrame with columns: user_id, movie_id, rating, timestamp.
    """
    import psycopg2

    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "recdb"),
        user=os.environ.get("POSTGRES_USER", "recuser"),
        password=os.environ.get("POSTGRES_PASSWORD", "recpass"),
    )
    df = pd.read_sql("SELECT user_id, movie_id, rating, timestamp FROM interactions", conn)
    conn.close()
    logger.info("Loaded %d ratings from PostgreSQL.", len(df))
    return df


def build_sparse_matrix(
    df: pd.DataFrame,
) -> tuple[csr_matrix, dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """Build sparse user-item rating matrix.

    Args:
        df: DataFrame with user_id, movie_id, rating columns.

    Returns:
        Tuple of (sparse_matrix, user_to_idx, item_to_idx, idx_to_user, idx_to_item).
    """
    unique_users = sorted(df["user_id"].unique())
    unique_items = sorted(df["movie_id"].unique())

    user_to_idx = {uid: i for i, uid in enumerate(unique_users)}
    item_to_idx = {mid: i for i, mid in enumerate(unique_items)}
    idx_to_user = {i: uid for uid, i in user_to_idx.items()}
    idx_to_item = {i: mid for mid, i in item_to_idx.items()}

    row_indices = df["user_id"].map(user_to_idx).values
    col_indices = df["movie_id"].map(item_to_idx).values
    ratings = df["rating"].values.astype(np.float32)

    matrix = csr_matrix(
        (ratings, (row_indices, col_indices)),
        shape=(len(unique_users), len(unique_items)),
    )
    logger.info(
        "Built sparse matrix: %d users x %d items, %d non-zero entries.",
        matrix.shape[0],
        matrix.shape[1],
        matrix.nnz,
    )
    return matrix, user_to_idx, item_to_idx, idx_to_user, idx_to_item


def train_svd(matrix: csr_matrix, n_components: int = 50) -> TruncatedSVD:
    """Train TruncatedSVD on the user-item matrix.

    Args:
        matrix: Sparse user-item rating matrix.
        n_components: Number of latent factors.

    Returns:
        Fitted TruncatedSVD model.
    """
    logger.info("Training TruncatedSVD with %d components...", n_components)
    svd = TruncatedSVD(n_components=n_components, random_state=42)
    svd.fit(matrix)
    explained = svd.explained_variance_ratio_.sum()
    logger.info("SVD training complete. Explained variance: %.4f", explained)
    return svd


def save_artifacts(
    svd: TruncatedSVD,
    matrix: csr_matrix,
    user_to_idx: dict[int, int],
    item_to_idx: dict[int, int],
    movie_metadata: dict[int, dict[str, str]],
    output_dir: str,
    n_components: int,
) -> None:
    """Save model artifacts to disk.

    Args:
        svd: Trained SVD model.
        matrix: User-item matrix.
        user_to_idx: User ID to matrix index mapping.
        item_to_idx: Movie ID to matrix index mapping.
        movie_metadata: Movie metadata dict.
        output_dir: Directory to save files.
        n_components: Number of SVD components used.
    """
    os.makedirs(output_dir, exist_ok=True)

    # User factors: transform the matrix
    user_factors = svd.transform(matrix)
    # Item factors: transpose of components
    item_factors = svd.components_.T

    np.save(os.path.join(output_dir, "user_factors.npy"), user_factors)
    np.save(os.path.join(output_dir, "item_factors.npy"), item_factors)

    # Save mappings as JSON (convert int keys to strings for JSON)
    user_map = {str(k): v for k, v in user_to_idx.items()}
    item_map = {str(k): v for k, v in item_to_idx.items()}

    with open(os.path.join(output_dir, "user_map.json"), "w") as f:
        json.dump(user_map, f)
    with open(os.path.join(output_dir, "item_map.json"), "w") as f:
        json.dump(item_map, f)

    # Save movie titles
    movie_titles = {str(k): v for k, v in movie_metadata.items()}
    with open(os.path.join(output_dir, "movie_titles.json"), "w") as f:
        json.dump(movie_titles, f)

    # Save metadata
    metadata = {
        "n_users": user_factors.shape[0],
        "n_items": item_factors.shape[0],
        "n_components": n_components,
        "explained_variance": float(svd.explained_variance_ratio_.sum()),
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(output_dir, "model_metadata.json"), "w") as f:
        json.dump(metadata, f, indent=2)

    logger.info(
        "Saved artifacts to %s: user_factors=%s, item_factors=%s",
        output_dir,
        user_factors.shape,
        item_factors.shape,
    )


def precompute_recommendations(
    user_factors: np.ndarray,
    item_factors: np.ndarray,
    user_to_idx: dict[int, int],
    item_to_idx: dict[int, int],
    movie_metadata: dict[int, dict[str, str]],
    ratings_df: pd.DataFrame,
    redis_client: redis.Redis,
    top_users: int = 1000,
    top_n: int = 20,
) -> None:
    """Precompute and cache top-N recommendations for the most active users.

    Args:
        user_factors: User factor matrix.
        item_factors: Item factor matrix.
        user_to_idx: User ID to index mapping.
        item_to_idx: Item ID to index mapping.
        movie_metadata: Movie metadata dict.
        ratings_df: Ratings dataframe for determining active users.
        redis_client: Redis client for caching.
        top_users: Number of top users to precompute for.
        top_n: Number of recommendations per user.
    """
    # Find top users by number of ratings
    user_counts = ratings_df["user_id"].value_counts().head(top_users)
    active_users = user_counts.index.tolist()

    idx_to_item = {v: int(k) for k, v in item_to_idx.items()}

    logger.info("Precomputing recs for top %d users...", len(active_users))
    pipe = redis_client.pipeline()
    cached = 0

    for user_id in active_users:
        if user_id not in user_to_idx:
            continue

        user_idx = user_to_idx[user_id]
        scores = user_factors[user_idx] @ item_factors.T

        # Get top-N item indices
        top_indices = np.argsort(scores)[::-1][:top_n]

        recs = []
        for idx in top_indices:
            movie_id = idx_to_item.get(idx)
            if movie_id is None:
                continue
            meta = movie_metadata.get(movie_id, {"title": "Unknown", "genres": ""})
            recs.append({
                "movie_id": movie_id,
                "score": round(float(scores[idx]), 4),
                "title": meta["title"],
                "genres": meta["genres"],
            })

        pipe.set(f"recs:{user_id}", json.dumps(recs), ex=3600)
        cached += 1

        if cached % 200 == 0:
            pipe.execute()
            pipe = redis_client.pipeline()
            logger.info("Cached recs for %d / %d users", cached, len(active_users))

    pipe.execute()
    logger.info("Precomputed recs for %d users.", cached)


def main() -> None:
    parser = argparse.ArgumentParser(description="Train CineRank recommendation model")
    parser.add_argument("--data-source", choices=["csv", "postgres"], default="csv")
    parser.add_argument("--csv-path", default="./ml-32m/ratings.csv")
    parser.add_argument("--movies-path", default="./ml-32m/movies.csv")
    parser.add_argument("--n-components", type=int, default=50)
    parser.add_argument("--output-dir", default="./models")
    parser.add_argument("--top-users", type=int, default=1000)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--max-ratings", type=int, default=0, help="Subsample ratings (0 = all)")
    args = parser.parse_args()

    start_time = time.time()

    # Load ratings
    if args.data_source == "csv":
        df = load_ratings_csv(args.csv_path, args.max_ratings)
    else:
        df = load_ratings_postgres()

    # Load movie metadata
    movie_metadata = load_movie_metadata(args.movies_path)

    # Build matrix and train
    matrix, user_to_idx, item_to_idx, idx_to_user, idx_to_item = build_sparse_matrix(df)
    svd = train_svd(matrix, args.n_components)

    # Save artifacts
    save_artifacts(svd, matrix, user_to_idx, item_to_idx, movie_metadata, args.output_dir, args.n_components)

    # Precompute recs if Redis is available
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))
    try:
        redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        redis_client.ping()

        user_factors = np.load(os.path.join(args.output_dir, "user_factors.npy"))
        item_factors = np.load(os.path.join(args.output_dir, "item_factors.npy"))

        precompute_recommendations(
            user_factors, item_factors, user_to_idx, item_to_idx,
            movie_metadata, df, redis_client, args.top_users, args.top_n,
        )
    except redis.ConnectionError:
        logger.warning("Redis not available — skipping precomputation. Run again with Redis to cache recs.")

    elapsed = time.time() - start_time
    logger.info("Training complete in %.1fs.", elapsed)


if __name__ == "__main__":
    main()
