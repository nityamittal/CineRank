# CLAUDE.md — CineRank: Recommendation Engine

## Goal

Train a collaborative filtering model on MovieLens data using SVD, save model artifacts, and provide a Python module that generates personalized recommendations with sub-100ms latency.

## Files

### `recommendation_engine/train_model.py`

Batch training script. Run periodically (or once for demo).

#### Steps

1. **Load data**: Read interactions from PostgreSQL or directly from `ratings.csv`
2. **Build sparse matrix**: `scipy.sparse.csr_matrix` of shape `(n_users, n_items)` with ratings as values
3. **Create ID mappings**: `user_id → matrix_row_index` and `movie_id → matrix_col_index` (and reverse)
4. **Apply TruncatedSVD**: `sklearn.decomposition.TruncatedSVD(n_components=50)`
   - Fit on the user-item matrix
   - Extract user factors: `svd.transform(matrix)` → shape `(n_users, 50)`
   - Extract item factors: `svd.components_.T` → shape `(n_items, 50)`
5. **Save artifacts** to `models/` directory:
   - `user_factors.npy` — numpy array
   - `item_factors.npy` — numpy array
   - `user_map.json` — `{user_id: row_index}`
   - `item_map.json` — `{movie_id: col_index}`
   - `movie_titles.json` — `{movie_id: {"title": "...", "genres": "..."}}`
   - `model_metadata.json` — `{"n_users": ..., "n_items": ..., "n_components": 50, "trained_at": "..."}`
6. **Precompute top-N recs** for the top 1000 most active users and cache in Redis:
   - Key: `recs:{user_id}` → JSON string of `[{"movie_id": 123, "score": 0.95, "title": "..."}, ...]`
   - TTL: 3600 seconds (1 hour)

#### CLI Arguments

```
--data-source   "postgres" or "csv" (default: csv)
--csv-path      Path to ratings.csv (default: /data/ml-32m/ratings.csv)
--movies-path   Path to movies.csv (default: /data/ml-32m/movies.csv)
--n-components  SVD components (default: 50)
--output-dir    Where to save model files (default: ./models)
--top-users     Number of top users to precompute recs for (default: 1000)
--top-n         Number of recommendations per user (default: 20)
```

#### Performance Notes

- MovieLens 32M has ~200K users and ~87K movies
- TruncatedSVD on this size: ~2–5 minutes on a modern laptop
- For faster dev iteration, add `--max-ratings N` flag to subsample

---

### `recommendation_engine/model.py`

Runtime inference module. Imported by the API.

#### Class: `RecommendationModel`

```python
class RecommendationModel:
    def __init__(self, model_dir: str = "./models", redis_client=None):
        """Load model artifacts from disk and connect to Redis."""
        
    def get_recommendations(self, user_id: int, n: int = 10, 
                            exclude_seen: bool = True) -> list[dict]:
        """
        Get top-N movie recommendations for a user.
        
        Strategy:
        1. Check Redis cache: recs:{user_id}
        2. If cache hit: return top N from cached list
        3. If cache miss but user exists in model:
           a. Get user factor vector
           b. Compute dot product with all item factors
           c. Exclude movies user has already rated (from Redis recent_movies or model)
           d. Sort by score descending, take top N
           e. Enrich with movie titles and genres
           f. Cache result in Redis (TTL 1 hour)
        4. If user not in model (cold start):
           a. Get user's genre preferences from Redis (genre_counts)
           b. Return popular movies in preferred genres
           c. If no genre data either: return globally popular movies
        
        Returns: [{"movie_id": int, "title": str, "score": float, "genres": str}, ...]
        """
    
    def get_similar_movies(self, movie_id: int, n: int = 10) -> list[dict]:
        """
        Find N most similar movies using cosine similarity on item factors.
        
        1. Get item factor vector for movie_id
        2. Compute cosine similarity against all other item factors
        3. Return top N (excluding the query movie itself)
        """
    
    def get_popular_movies(self, n: int = 10, genre: str = None) -> list[dict]:
        """Fallback: return most-rated movies, optionally filtered by genre."""
```

#### Cold Start Strategy

This is important for demonstrating system design thinking:

- **New user with some activity (in Redis but not in model)**: Use content-based filtering on their genre preferences
- **Completely new user (no data at all)**: Return globally popular movies
- **New movie (not in model)**: Use content-based similarity on genre tags

---

### `recommendation_engine/utils.py`

```python
def parse_genres(genres_str: str) -> list[str]:
    """Parse pipe-separated genres: 'Action|Comedy|Drama' → ['Action', 'Comedy', 'Drama']"""

def cosine_similarity_batch(vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a single vector and all rows of a matrix."""

def load_movie_metadata(movies_path: str) -> dict:
    """Load movies.csv into {movie_id: {"title": str, "genres": str}} dict."""
```

---

### requirements.txt

```
numpy>=1.24.0
scipy>=1.11.0
scikit-learn>=1.3.0
pandas>=2.0.0
redis>=5.0.0
psycopg2-binary>=2.9.0
```

### Dockerfile

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "train_model.py"]
```

## Verification

```bash
# Train the model
python train_model.py --data-source csv --csv-path ../data/ml-32m/ratings.csv

# Check artifacts
ls models/
# user_factors.npy  item_factors.npy  user_map.json  item_map.json  movie_titles.json  model_metadata.json

# Quick test
python -c "
from model import RecommendationModel
m = RecommendationModel('./models')
recs = m.get_recommendations(user_id=1, n=5)
for r in recs:
    print(f'{r[\"title\"]} ({r[\"score\"]:.3f})')
"
```

## Design Rationale

- **SVD over neural methods**: Fast to train, no GPU needed, well-understood, good baseline performance on MovieLens
- **Precomputed cache**: Most users will have cached recs. Only cold-start or cache-miss users trigger real-time computation
- **Hybrid fallback**: Shows system design maturity — real systems always need cold-start handling
