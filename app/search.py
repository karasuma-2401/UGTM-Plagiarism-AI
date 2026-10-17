import json

from app.db import connect


def vector_literal(vector) -> str:
    return json.dumps([float(value) for value in vector], separators=(",", ":"))


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

    return sorted(
        merged.values(),
        key=lambda item: item["rrf_score"],
        reverse=True,
    )[:fused_k]

