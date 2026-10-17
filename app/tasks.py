import logging

from psycopg.types.json import Jsonb

from app.celery_app import celery_app
from app.config import settings
from app.db import connect, get_job, set_status
from app.documents import extract_pages, make_chunks
from app.models import encode_texts, rerank_pairs
from app.search import retrieve, vector_literal

logger = logging.getLogger(__name__)


def parse_job(job):
    chunks = make_chunks(extract_pages(job["file_path"]))
    if not chunks:
        raise ValueError("EMPTY_TEXT")
    return chunks


@celery_app.task(name="app.tasks.index_source")
def index_source(job_id: str):
    set_status(job_id, "processing")
    try:
        job = get_job(job_id)
        chunks = parse_job(job)
        embeddings = encode_texts([chunk.text for chunk in chunks])

        with connect() as con:
            row = con.execute(
                """
                INSERT INTO integrity.sources
                  (corpus_id, source_key, version, filename, file_sha256)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (corpus_id, source_key, version)
                DO UPDATE SET filename = EXCLUDED.filename,
                              file_sha256 = EXCLUDED.file_sha256
                RETURNING id
                """,
                (
                    job["corpus_id"],
                    job["document_key"],
                    job["version"],
                    job["filename"],
                    job["file_sha256"],
                ),
            ).fetchone()
            source_id = row["id"]
            con.execute(
                "DELETE FROM integrity.chunks WHERE source_id = %s",
                (source_id,),
            )
            for chunk, vector in zip(chunks, embeddings):
                con.execute(
                    """
                    INSERT INTO integrity.chunks
                      (source_id, chunk_no, page_number, start_offset,
                       end_offset, content, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    (
                        source_id,
                        chunk.chunk_no,
                        chunk.page_number,
                        chunk.start_offset,
                        chunk.end_offset,
                        chunk.text,
                        vector_literal(vector),
                    ),
                )

            # Chỉ chuyển active version sau khi toàn bộ indexing thành công.
            con.execute(
                """
                UPDATE integrity.sources SET is_active = FALSE
                WHERE corpus_id = %s AND source_key = %s AND id <> %s
                """,
                (job["corpus_id"], job["document_key"], source_id),
            )
            con.execute(
                """
                UPDATE integrity.sources
                SET is_active = TRUE, indexed_at = now()
                WHERE id = %s
                """,
                (source_id,),
            )
            con.execute(
                """
                UPDATE integrity.jobs
                SET status = 'completed', updated_at = now(), report = %s
                WHERE id = %s::uuid
                """,
                (
                    Jsonb(
                        {
                            "indexed_chunks": len(chunks),
                            "source_id": job["document_key"],
                        }
                    ),
                    job_id,
                ),
            )
    except Exception:
        logger.exception("Index job failed: %s", job_id)
        set_status(job_id, "failed", "INDEX_ERROR")


@celery_app.task(name="app.tasks.scan_document")
def scan_document(job_id: str):
    set_status(job_id, "processing")
    try:
        job = get_job(job_id)
        with connect() as con:
            count = con.execute(
                """
                SELECT count(*) AS n FROM integrity.sources
                WHERE corpus_id = %s AND is_active = TRUE
                """,
                (job["corpus_id"],),
            ).fetchone()["n"]
        if count == 0:
            raise ValueError("CORPUS_EMPTY")

        query_chunks = parse_job(job)
        vectors = encode_texts([chunk.text for chunk in query_chunks])
        all_findings: list[dict] = []
        for query_chunk, vector in zip(query_chunks, vectors):
            candidates = retrieve(query_chunk.text, vector, job["corpus_id"])
            if not candidates:
                continue
            pairs = [
                [query_chunk.text, item["source_text"]] for item in candidates
            ]
            scores = rerank_pairs(pairs)
            for item, score in zip(candidates, scores):
                # Heuristic demo, không phải ngưỡng kết luận đạo văn.
                lexical = item["lexical_score"] or 0.0
                dense = item["dense_score"] or 0.0
                review = lexical >= 0.65 or (dense >= 0.68 and score >= 0.67)
                if not review:
                    continue
                all_findings.append(
                    {
                        "submission_page": query_chunk.page_number,
                        "submission_start": query_chunk.start_offset,
                        "submission_end": query_chunk.end_offset,
                        "submission_text": query_chunk.text,
                        **item,
                        "reranker_score": round(score, 4),
                        "review_required": True,
                    }
                )

        all_findings.sort(
            key=lambda finding: finding["reranker_score"],
            reverse=True,
        )
        findings = all_findings[:200]
        report = {
            "schema_version": 1,
            "job_id": job_id,
            "corpus_id": job["corpus_id"],
            "submission_id": job["document_key"],
            "submission_version": job["version"],
            "model": {
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
            },
            "summary": {
                "scanned_chunks": len(query_chunks),
                "flagged_pairs": len(all_findings),
                "findings_returned": len(findings),
                "matched_sources": len(
                    {finding["source_key"] for finding in all_findings}
                ),
            },
            "findings": findings,
            "disclaimer": (
                "Similarity evidence only; teachers must verify sources, "
                "citations and context. No automatic plagiarism verdict."
            ),
        }
        with connect() as con:
            con.execute(
                """
                UPDATE integrity.jobs
                SET status = 'completed', report = %s, updated_at = now()
                WHERE id = %s::uuid
                """,
                (Jsonb(report), job_id),
            )
    except Exception:
        logger.exception("Scan job failed: %s", job_id)
        set_status(job_id, "failed", "SCAN_ERROR")

