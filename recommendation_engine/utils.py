"""Utility functions for the recommendation engine."""

import numpy as np
import pandas as pd


def parse_genres(genres_str: str) -> list[str]:
    """Parse pipe-separated genres string.

    Args:
        genres_str: Pipe-separated genres, e.g. 'Action|Comedy|Drama'.

    Returns:
        List of genre strings, e.g. ['Action', 'Comedy', 'Drama'].
    """
    if not genres_str or genres_str == "(no genres listed)":
        return []
    return genres_str.split("|")


def cosine_similarity_batch(vector: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between a single vector and all rows of a matrix.

    Args:
        vector: 1-D array of shape (d,).
        matrix: 2-D array of shape (n, d).

    Returns:
        1-D array of shape (n,) with cosine similarities.
    """
    vector_norm = np.linalg.norm(vector)
    if vector_norm == 0:
        return np.zeros(matrix.shape[0])

    matrix_norms = np.linalg.norm(matrix, axis=1)
    # Avoid division by zero
    matrix_norms = np.where(matrix_norms == 0, 1e-10, matrix_norms)

    dot_products = matrix @ vector
    similarities = dot_products / (matrix_norms * vector_norm)
    return similarities


def load_movie_metadata(movies_path: str) -> dict[int, dict[str, str]]:
    """Load movies.csv into a lookup dict.

    Args:
        movies_path: Path to movies.csv file.

    Returns:
        Dict mapping movie_id to {"title": str, "genres": str}.
    """
    df = pd.read_csv(movies_path)
    df.columns = [c.strip().lower() for c in df.columns]
    df.rename(columns={"movieid": "movie_id"}, inplace=True)

    metadata: dict[int, dict[str, str]] = {}
    for _, row in df.iterrows():
        metadata[int(row["movie_id"])] = {
            "title": str(row["title"]),
            "genres": str(row.get("genres", "")),
        }
    return metadata
