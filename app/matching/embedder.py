from sentence_transformers import SentenceTransformer
import os
import numpy as np

# Cache sentence-transformers model instance
_model_instance = None

def get_embedder():
    global _model_instance
    if _model_instance is None:
        # Using paraphrase-multilingual-MiniLM-L12-v2 as requested (high multilingual capability)
        # It handles Vietnamese very well.
        _model_instance = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model_instance

def get_embedding(text: str):
    """
    Generate embedding for given text.
    """
    model = get_embedder()
    return model.encode(text, convert_to_numpy=True)

def calculate_similarity(embedding1, embedding2) -> float:
    """
    Calculate cosine similarity between two embeddings.
    """
    norm1 = np.linalg.norm(embedding1)
    norm2 = np.linalg.norm(embedding2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(np.dot(embedding1, embedding2) / (norm1 * norm2))
