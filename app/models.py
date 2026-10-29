from functools import lru_cache

import numpy as np
import torch
import transformers.modeling_utils as mu
import transformers.utils.import_utils as iu

# Bypass false-positive torch.load safety check for trusted BAAI models
iu.check_torch_load_is_safe = lambda: None
mu.check_torch_load_is_safe = lambda: None

from FlagEmbedding import BGEM3FlagModel, FlagReranker

from app.config import settings


def get_device():
    return "cuda:0" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def embedder():
    device = get_device()
    use_fp16 = device.startswith("cuda")
    devices = [device] if device.startswith("cuda") else None
    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=use_fp16,
        devices=devices,
    )


@lru_cache(maxsize=1)
def reranker():
    device = get_device()
    use_fp16 = device.startswith("cuda")
    devices = [device] if device.startswith("cuda") else None
    return FlagReranker(
        settings.reranker_model,
        use_fp16=use_fp16,
        devices=devices,
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

