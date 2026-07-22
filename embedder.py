from sentence_transformers import SentenceTransformer
import numpy as np

model = SentenceTransformer('all-MiniLM-L6-v2')

def get_embedding(text: str) -> np.ndarray:
    """Return normalized embedding vector."""
    return model.encode(text, normalize_embeddings=True)

def cosine_similarity(vec1: np.ndarray, vec2: np.ndarray) -> float:
    """Cosine similarity between two normalized vectors."""
    return np.dot(vec1, vec2)