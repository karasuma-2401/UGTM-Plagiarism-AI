import json

from app.db import connect


def vector_literal(vector) -> str:
    # Cast ::vector ở SQL; không dựng câu SQL bằng nối chuỗi.
    return json.dumps([float(x) for x in vector], separators=(",", ":"))


def retrieve(
    query_text: str,
    query_vector,
    corpus_id: str,
    top_k: int = 6,
    fused_k: int = 5,
) -> list[dict]:
    vector = vector_literal(query_vector)
    with connect() as con:
        dense = con.execute(
            """
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename,
                   1 - (c.embedding <=> %s::vector) AS dense_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, corpus_id, vector, top_k),
        ).fetchall()

        lexical = con.execute(
            """
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename,
                   similarity(c.content, %s) AS lexical_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE
            ORDER BY similarity(c.content, %s) DESC
            LIMIT %s
            """,
            (query_text, corpus_id, query_text, top_k),
        ).fetchall()

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
                    "source_page": row["page_number"],
                    "source_start": row["start_offset"],
                    "source_end": row["end_offset"],
                    "source_text": row["content"],
                    "rrf_score": 0.0,
                    "dense_score": None,
                    "lexical_score": None,
                },
            )
            item["rrf_score"] += 1.0 / (60 + rank)
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
