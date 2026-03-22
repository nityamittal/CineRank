"""Optional: preload Redis cache with movie metadata for fast lookups."""

import json
import logging
import os
import sys

import pandas as pd
import redis

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    movies_path = os.environ.get("MOVIES_PATH", "./ml-32m/movies.csv")
    redis_host = os.environ.get("REDIS_HOST", "localhost")
    redis_port = int(os.environ.get("REDIS_PORT", 6379))

    logger.info("Connecting to Redis at %s:%d", redis_host, redis_port)
    r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)

    try:
        r.ping()
    except redis.ConnectionError as exc:
        logger.error("Cannot connect to Redis: %s", exc)
        sys.exit(1)

    logger.info("Loading movies from %s", movies_path)
    df = pd.read_csv(movies_path)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"movieid": "movie_id"}, inplace=True)

    pipe = r.pipeline()
    for _, row in df.iterrows():
        movie_data = json.dumps({
            "title": row["title"],
            "genres": row["genres"],
        })
        pipe.set(f"movie:{row['movie_id']}:info", movie_data)

    pipe.execute()
    logger.info("Cached %d movies in Redis.", len(df))


if __name__ == "__main__":
    main()
