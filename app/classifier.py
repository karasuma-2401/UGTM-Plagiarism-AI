import json
from functools import lru_cache
from pathlib import Path

import joblib

from app.config import settings
from app.features import FEATURE_NAMES, FEATURE_SCHEMA, feature_vector


@lru_cache(maxsize=1)
def load_classifier():
    # Chỉ dùng artifact do pipeline nội bộ tạo và đã được kiểm chứng.
    # joblib/pickle KHÔNG an toàn với file không đáng tin cậy.
    path = Path(settings.classifier_path)
    manifest_path = path.with_suffix(".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise RuntimeError("CLASSIFIER_ARTIFACT_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("feature_schema") != FEATURE_SCHEMA
        or manifest.get("feature_names") != FEATURE_NAMES
    ):
        raise RuntimeError("CLASSIFIER_FEATURE_SCHEMA_MISMATCH")
    if (
        manifest.get("embedding_model") != settings.embedding_model
        or manifest.get("reranker_model") != settings.reranker_model
    ):
        raise RuntimeError("CLASSIFIER_BASE_MODEL_MISMATCH")
    model = joblib.load(path)
    return model, manifest


def score_candidate(features: dict):
    if not settings.classifier_enabled:
        return None
    model, manifest = load_classifier()
    # predict_proba là điểm mô hình trên dữ liệu huấn luyện, KHÔNG phải
    # xác suất vi phạm quy chế hay đạo văn trong thực tế.
    score = float(model.predict_proba([feature_vector(features)])[0, 1])
    return {
        "score": score,
        "threshold": float(manifest["threshold"]),
        "version": str(manifest["model_version"]),
    }

