from functools import lru_cache

import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker

from app.config import settings


def assert_gpu():
    if not torch.cuda.is_available():
        raise RuntimeError("GPU CUDA is required by this configuration")


@lru_cache(maxsize=1)
def embedder():
    assert_gpu()
    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=True,
        devices=["cuda:0"],
    )


@lru_cache(maxsize=1)
def reranker():
    assert_gpu()
    return FlagReranker(
        settings.reranker_model,
        use_fp16=True,
        devices=["cuda:0"],
    )


def encode_texts(texts: list[str], batch_size: int = 8) -> np.ndarray:
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)
    data = embedder().encode(
        texts,
        batch_size=batch_size,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return np.asarray(data["dense_vecs"], dtype=np.float32)


def rerank_pairs(pairs: list[list[str]], batch_size: int = 8) -> list[float]:
    if not pairs:
        return []
    scores = reranker().compute_score(
        pairs,
        batch_size=batch_size,
        normalize=True,
        max_length=512,
    )
    if isinstance(scores, (float, int)):
        return [float(scores)]
    return [float(value) for value in scores]

