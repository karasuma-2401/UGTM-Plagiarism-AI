import json
from pathlib import Path
from typing import Any

from app.db import connect

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RETRIEVAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "retrieval_config.json"


def load_retrieval_config() -> dict[str, Any]:
    """Load production retrieval configuration from configs/retrieval_config.json."""
    if RETRIEVAL_CONFIG_PATH.is_file():
        try:
            return json.loads(RETRIEVAL_CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "top_k_per_branch": 30,
        "fused_k": 25,
        "rrf_k": 60,
        "prefilter": {"enabled": False, "mode": "none"},
    }


def vector_literal(vector) -> str:
    # Cast ::vector ở SQL; không dựng câu SQL bằng nối chuỗi.
    return json.dumps([float(x) for x in vector], separators=(",", ":"))


def retrieve(
    query_text: str,
    query_vector,
    corpus_id: str,
    top_k: int | None = None,
    fused_k: int | None = None,
    rrf_k: int | None = None,
    exclude_source_keys: list[str] | None = None,
    exclude_document_family: str | None = None,
) -> list[dict]:
    query_text = (query_text or "").replace("\x00", "")
    cfg = load_retrieval_config()
    if top_k is None:
        top_k = int(cfg.get("top_k_per_branch", 30))
    if fused_k is None:
        fused_k = int(cfg.get("fused_k", 25))
    if rrf_k is None:
        rrf_k = int(cfg.get("rrf_k", 60))

    vector = vector_literal(query_vector)

    extra_filters: list[str] = []
    extra_params: list[object] = []
    if exclude_source_keys:
        extra_filters.append("AND NOT (s.source_key = ANY(%s))")
        extra_params.append(list(exclude_source_keys))
    if exclude_document_family:
        extra_filters.append("AND (s.document_family IS NULL OR s.document_family != %s)")
        extra_params.append(exclude_document_family)

    filter_clause = (" " + " ".join(extra_filters)) if extra_filters else ""

    with connect() as con:
        dense_sql = f"""
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename, s.document_family,
                   1 - (c.embedding <=> %s::vector) AS dense_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE{filter_clause}
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """
        dense_params = (vector, corpus_id, *extra_params, vector, top_k)
        dense = con.execute(dense_sql, dense_params).fetchall()

        lexical_sql = f"""
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename, s.document_family,
                   similarity(c.content, %s) AS lexical_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE{filter_clause}
            ORDER BY similarity(c.content, %s) DESC
            LIMIT %s
        """
        lexical_params = (query_text, corpus_id, *extra_params, query_text, top_k)
        lexical = con.execute(lexical_sql, lexical_params).fetchall()

    merged: dict[int, dict] = {}
    for branch, rows in (("dense", dense), ("lexical", lexical)):
        for rank, row in enumerate(rows, start=1):
            item = merged.setdefault(
                row["id"],
                {
                    "chunk_id": row["id"],
                    "source_key": row["source_key"],
                    "source_version": row["version"],
                    "source_filename": row["filename"],
                    "document_family": row["document_family"] if "document_family" in row else None,
                    "source_page": row["page_number"],
                    "source_start": row["start_offset"],
                    "source_end": row["end_offset"],
                    "source_text": row["content"],
                    "rrf_score": 0.0,
                    "dense_score": None,
                    "lexical_score": None,
                },
            )
            item["rrf_score"] += 1.0 / (rrf_k + rank)
            score_key = f"{branch}_score"
            item[score_key] = float(row[score_key])

    # Chốt score từ CẢ HAI phương pháp trên TỪNG ứng viên, không giữ NULL
    # chỉ vì candidate được tìm thấy bởi một nhánh. Điều này quan trọng
    # để vector feature lúc train và inference cùng một định nghĩa.
    selected = sorted(
        merged.values(), key=lambda x: x["rrf_score"], reverse=True
    )[:fused_k]
    if not selected:
        return []

    ids = [int(x["chunk_id"]) for x in selected]
    with connect() as con:
        scores = con.execute(
            """
            SELECT id,
                   1 - (embedding <=> %s::vector) AS dense_score,
                   similarity(content, %s) AS lexical_score,
                   chunk_no
            FROM integrity.chunks
            WHERE id = ANY(%s::bigint[])
            """,
            (vector, query_text, ids),
        ).fetchall()

    score_by_id = {row["id"]: row for row in scores}
    for item in selected:
        row = score_by_id[item["chunk_id"]]
        item["dense_score"] = float(row["dense_score"])
        item["lexical_score"] = float(row["lexical_score"])
        item["source_chunk_no"] = int(row["chunk_no"])
    return selected
