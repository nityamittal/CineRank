"""Seed PostgreSQL with MovieLens 32M data (movies and ratings)."""

import argparse
import logging
import os
import sys
import time

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_connection() -> psycopg2.extensions.connection:
    """Create a PostgreSQL connection from environment variables."""
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", 5432)),
        dbname=os.environ.get("POSTGRES_DB", "recdb"),
        user=os.environ.get("POSTGRES_USER", "recuser"),
        password=os.environ.get("POSTGRES_PASSWORD", "recpass"),
    )


def seed_movies(conn: psycopg2.extensions.connection, movies_path: str) -> int:
    """Load movies.csv into the movies table.

    Returns the number of movies inserted.
    """
    logger.info("Loading movies from %s", movies_path)
    df = pd.read_csv(movies_path)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"movieid": "movie_id"}, inplace=True)

    rows = list(df[["movie_id", "title", "genres"]].itertuples(index=False, name=None))
    logger.info("Inserting %d movies...", len(rows))

    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO movies (movie_id, title, genres) VALUES %s ON CONFLICT (movie_id) DO NOTHING",
            rows,
            page_size=5000,
        )
    conn.commit()
    logger.info("Movies seeded successfully.")
    return len(rows)


def seed_ratings(
    conn: psycopg2.extensions.connection,
    ratings_path: str,
    max_ratings: int = 1_000_000,
    batch_size: int = 5000,
) -> int:
    """Load ratings.csv into the users and interactions tables.

    Args:
        conn: PostgreSQL connection.
        ratings_path: Path to ratings.csv.
        max_ratings: Maximum number of ratings to load (0 = all).
        batch_size: INSERT batch size.

    Returns the number of interactions inserted.
    """
    logger.info("Loading ratings from %s (limit=%s)", ratings_path, max_ratings or "all")

    nrows = max_ratings if max_ratings > 0 else None
    df = pd.read_csv(ratings_path, nrows=nrows)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(
        columns={"userid": "user_id", "movieid": "movie_id"},
        inplace=True,
    )

    # Insert unique users first
    unique_users = sorted(df["user_id"].unique())
    logger.info("Inserting %d unique users...", len(unique_users))
    with conn.cursor() as cur:
        execute_values(
            cur,
            "INSERT INTO users (user_id) VALUES %s ON CONFLICT (user_id) DO NOTHING",
            [(uid,) for uid in unique_users],
            page_size=5000,
        )
    conn.commit()

    # Insert interactions in batches
    total = len(df)
    inserted = 0
    logger.info("Inserting %d interactions in batches of %d...", total, batch_size)

    with conn.cursor() as cur:
        for start in range(0, total, batch_size):
            batch = df.iloc[start : start + batch_size]
            rows = list(
                batch[["user_id", "movie_id", "rating", "timestamp"]].itertuples(
                    index=False, name=None
                )
            )
            execute_values(
                cur,
                "INSERT INTO interactions (user_id, movie_id, rating, timestamp) VALUES %s",
                rows,
                page_size=batch_size,
            )
            inserted += len(rows)
            if inserted % 50_000 == 0 or inserted == total:
                logger.info("Progress: %d / %d interactions (%.1f%%)", inserted, total, inserted / total * 100)
        conn.commit()

    logger.info("Ratings seeded successfully.")
    return inserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed PostgreSQL with MovieLens data")
    parser.add_argument("--ratings-path", default="./ml-32m/ratings.csv", help="Path to ratings.csv")
    parser.add_argument("--movies-path", default="./ml-32m/movies.csv", help="Path to movies.csv")
    parser.add_argument("--max-ratings", type=int, default=1_000_000, help="Max ratings to load (0 = all)")
    parser.add_argument("--batch-size", type=int, default=5000, help="INSERT batch size")
    args = parser.parse_args()

    start_time = time.time()

    try:
        conn = get_connection()
    except psycopg2.OperationalError as exc:
        logger.error("Cannot connect to PostgreSQL: %s", exc)
        sys.exit(1)

    try:
        n_movies = seed_movies(conn, args.movies_path)
        n_interactions = seed_ratings(conn, args.ratings_path, args.max_ratings, args.batch_size)

        elapsed = time.time() - start_time
        logger.info(
            "Seeding complete in %.1fs — %d movies, %d interactions loaded.",
            elapsed,
            n_movies,
            n_interactions,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
