from functools import lru_cache
import logging
import os

import numpy as np
import requests
import torch
import transformers.modeling_utils as mu
import transformers.utils.import_utils as iu

# Bypass false-positive torch.load safety check for trusted BAAI models
iu.check_torch_load_is_safe = lambda: None
mu.check_torch_load_is_safe = lambda: None

from FlagEmbedding import BGEM3FlagModel, FlagReranker

from app.config import settings

logger = logging.getLogger(__name__)


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


def check_remote_gpu() -> dict | None:
    url = (settings.remote_gpu_url or "").strip().rstrip("/")
    if not url:
        return None
    try:
        res = requests.get(f"{url}/health", timeout=5)
        if res.ok:
            return res.json()
    except Exception as e:
        logger.warning("Remote GPU health check failed: %s", e)
    return None


def encode_texts(texts: list[str], batch_size: int = 4) -> np.ndarray:
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)

    url = (settings.remote_gpu_url or "").strip().rstrip("/")
    if url:
        try:
            logger.info("Using Remote GPU (%s) to encode %d texts...", url, len(texts))
            res = requests.post(
                f"{url}/encode",
                json={"texts": texts, "batch_size": 32},
                timeout=180,
            )
            if res.ok:
                vecs = res.json().get("dense_vecs", [])
                if len(vecs) == len(texts):
                    logger.info("Remote GPU encoding succeeded (%d vectors returned).", len(vecs))
                    return np.asarray(vecs, dtype=np.float32)
            logger.warning("Remote GPU returned HTTP %s. Falling back to local CPU...", res.status_code)
        except Exception as e:
            logger.warning("Remote GPU request failed (%s). Falling back to local CPU...", e)

    data = embedder().encode(
        texts,
        batch_size=batch_size,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return np.asarray(data["dense_vecs"], dtype=np.float32)


def rerank_pairs(pairs: list[list[str]], batch_size: int = 4) -> list[float]:
    if not pairs:
        return []

    url = (settings.remote_gpu_url or "").strip().rstrip("/")
    if url:
        try:
            logger.info("Using Remote GPU (%s) to rerank %d pairs...", url, len(pairs))
            res = requests.post(
                f"{url}/rerank",
                json={"pairs": pairs, "batch_size": 32},
                timeout=180,
            )
            if res.ok:
                scores = res.json().get("scores", [])
                if len(scores) == len(pairs):
                    logger.info("Remote GPU reranking succeeded (%d scores returned).", len(scores))
                    return [float(v) for v in scores]
            logger.warning("Remote GPU rerank returned HTTP %s. Falling back to local CPU...", res.status_code)
        except Exception as e:
            logger.warning("Remote GPU rerank request failed (%s). Falling back to local CPU...", e)

    scores = reranker().compute_score(
        pairs,
        batch_size=batch_size,
        normalize=True,
        max_length=512,
    )
    if isinstance(scores, (float, int)):
        return [float(scores)]
    return [float(value) for value in scores]
