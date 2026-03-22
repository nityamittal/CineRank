# CLAUDE.md — CineRank: Data & Seeding

## Goal

Seed PostgreSQL with movie metadata and interactions from the MovieLens 32M dataset which already exists locally.

## Dataset Location

The dataset is already downloaded and available at `ml-32m/` in the project root. **Do NOT download anything.**

Files present:
- `ml-32m/ratings.csv` — 32M ratings (userId, movieId, rating, timestamp) from 200,948 users
- `ml-32m/movies.csv` — 87,585 movies (movieId, title, genres)
- `ml-32m/tags.csv` — 2M tag applications (userId, movieId, tag, timestamp)
- `ml-32m/links.csv` — external IDs (movieId, imdbId, tmdbId)

Ratings are on a 5-star scale with half-star increments (0.5–5.0). Genres are pipe-separated (e.g. `Action|Comedy|Drama`).

## Files to Create

### `scripts/seed_postgres.py`

Python script that loads MovieLens data into PostgreSQL.

#### What it does:

1. Connect to PostgreSQL using env vars
2. Load `ml-32m/movies.csv`:
   - Parse each row
   - INSERT into `movies` table (movie_id, title, genres)
   - Use `ON CONFLICT DO NOTHING` for idempotency
3. Load `ml-32m/ratings.csv` (first N rows for dev, configurable):
   - Extract unique user_ids → INSERT into `users` table
   - Batch INSERT into `interactions` table (user_id, movie_id, rating, timestamp)
   - Use batch size of 5000 rows for performance
4. Print summary: total users, movies, interactions loaded

#### CLI Arguments:

```
--ratings-path   Path to ratings.csv (default: ./ml-32m/ratings.csv)
--movies-path    Path to movies.csv (default: ./ml-32m/movies.csv)
--max-ratings    Max ratings to load (default: 1000000 for dev, 0 = all)
--batch-size     INSERT batch size (default: 5000)
```

#### Dependencies:

```
psycopg2-binary>=2.9.0
pandas>=2.0.0
```

### `data/sample_events.json`

A small file with 10 hand-crafted events for quick testing without the full dataset:

```json
[
  {"user_id": 1, "movie_id": 1, "rating": 4.0, "timestamp": 964982703},
  {"user_id": 1, "movie_id": 50, "rating": 5.0, "timestamp": 964982931},
  {"user_id": 2, "movie_id": 1, "rating": 3.5, "timestamp": 964983100},
  {"user_id": 2, "movie_id": 110, "rating": 4.0, "timestamp": 964983200},
  {"user_id": 3, "movie_id": 1, "rating": 4.5, "timestamp": 964983300},
  {"user_id": 3, "movie_id": 260, "rating": 5.0, "timestamp": 964983400},
  {"user_id": 1, "movie_id": 260, "rating": 4.5, "timestamp": 964983500},
  {"user_id": 2, "movie_id": 318, "rating": 5.0, "timestamp": 964983600},
  {"user_id": 3, "movie_id": 50, "rating": 3.0, "timestamp": 964983700},
  {"user_id": 1, "movie_id": 318, "rating": 5.0, "timestamp": 964983800}
]
```

## Execution Order

```bash
# 1. Start infra
docker-compose up -d postgres

# 2. Seed database
python scripts/seed_postgres.py --max-ratings 1000000

# 3. Verify
docker exec -it $(docker-compose ps -q postgres) psql -U recuser -d recdb -c "
  SELECT 'users' as tbl, COUNT(*) FROM users
  UNION ALL
  SELECT 'movies', COUNT(*) FROM movies
  UNION ALL
  SELECT 'interactions', COUNT(*) FROM interactions;
"
```

## Notes

- For quick dev/testing, `--max-ratings 100000` loads in seconds
- For the full demo, use `--max-ratings 1000000` (loads in ~30 seconds)
- `sample_events.json` is checked into git; the actual dataset folder `ml-32m/` should be in `.gitignore`
- All other modules (kafka_producer, stream_processor, recommendation_engine) should reference `ml-32m/` as the data path
