import logging

from psycopg.types.json import Jsonb

from app.alignment import group_adjacent_findings
from app.celery_app import celery_app
from app.config import settings
from app.db import connect, get_job, set_status
from app.features import build_features

logger = logging.getLogger(__name__)


def parse_job(job):
    from pathlib import Path
    from app.documents import extract_pages, make_chunks
    from app.storage import download_to_temp_file

    with download_to_temp_file(
        job["file_path"],
        expected_sha256=job.get("file_sha256"),
        suffix=Path(job.get("filename", "")).suffix,
    ) as temp_path:
        chunks = make_chunks(extract_pages(str(temp_path)))
        if not chunks:
            raise ValueError("EMPTY_TEXT")
        return chunks


@celery_app.task(name="app.tasks.index_source")
def index_source(job_id: str):
    from app.inference import InferenceError
    from app.models import encode_texts
    from app.search import vector_literal

    set_status(job_id, "processing")
    try:
        job = get_job(job_id)
        chunks = parse_job(job)
        embeddings = encode_texts([x.text for x in chunks])
        with connect() as con:
            row = con.execute(
                """
                INSERT INTO integrity.sources
                  (corpus_id, source_key, version, filename, file_sha256)
                VALUES (%s,%s,%s,%s,%s)
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
                    VALUES (%s,%s,%s,%s,%s,%s,%s::vector)
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
            # Chỉ chuyển phiên bản active khi indexing hoàn tất.
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
                SET status = 'completed', updated_at = now(),
                    report = %s
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
    except InferenceError as exc:
        logger.error(
            "Index job %s failed due to inference error (%s): %s",
            job_id,
            exc.error_code,
            exc,
        )
        set_status(job_id, "failed", exc.error_code)
    except Exception:
        logger.exception("Index job failed: %s", job_id)
        set_status(job_id, "failed", "INDEX_ERROR")


@celery_app.task(name="app.tasks.scan_document")
def scan_document(job_id: str):
    from app.classifier import score_candidate
    from app.inference import InferenceError
    from app.models import encode_texts, rerank_pairs
    from app.search import retrieve

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
        vectors = encode_texts([x.text for x in query_chunks])
        all_findings: list[dict] = []

        # TỐI ƯU HÓA: Lọc thô ứng viên trước khi đưa vào mô hình Cross-Encoder.
        # Chỉ những cặp có độ tương đồng ban đầu tiềm năng mới cần rerank sâu.
        candidates_to_rerank: list[tuple] = []
        for query_chunk, vector in zip(query_chunks, vectors):
            candidates = retrieve(query_chunk.text, vector, job["corpus_id"])
            if not candidates:
                continue
            for item in candidates:
                dense = float(item["dense_score"])
                lexical = float(item["lexical_score"])
                # Bỏ qua 95% các đoạn không tương quan để tiết kiệm thời gian tính toán
                if dense >= 0.48 or lexical >= 0.25:
                    candidates_to_rerank.append((query_chunk, item))

        if candidates_to_rerank:
            pairs = [
                [chunk.text, item["source_text"]]
                for chunk, item in candidates_to_rerank
            ]
            scores = rerank_pairs(pairs, batch_size=16)
            for (query_chunk, item), score in zip(candidates_to_rerank, scores):
                lexical = float(item["lexical_score"])
                dense = float(item["dense_score"])
                features = build_features(
                    query_chunk.text,
                    item["source_text"],
                    dense,
                    float(score),
                )
                result = score_candidate(features)  # None khi feature flag OFF
                if result is None:
                    # Heuristic demo
                    review = lexical >= 0.65 or (dense >= 0.68 and score >= 0.67)
                    decision_source = "heuristic"
                else:
                    # Near-exact override giúp không bỏ qua copy gần nguyên văn.
                    review = (
                        result["score"] >= result["threshold"]
                        or lexical >= 0.92
                    )
                    decision_source = (
                        "near_exact_override"
                        if lexical >= 0.92
                        else "custom_classifier"
                    )
                if not review:
                    continue
                all_findings.append(
                    {
                        "submission_chunk_no": query_chunk.chunk_no,
                        "submission_page": query_chunk.page_number,
                        "submission_start": query_chunk.start_offset,
                        "submission_end": query_chunk.end_offset,
                        "submission_text": query_chunk.text,
                        **item,
                        "reranker_score": round(float(score), 4),
                        "classifier_score": (
                            round(result["score"], 4) if result else None
                        ),
                        "decision_source": decision_source,
                        "feature_values": features,
                        "review_required": True,
                    }
                )

        # Sắp xếp, giữ một vài bằng chứng có điểm tốt nhất trên mỗi chunk.
        all_findings.sort(key=lambda f: f["reranker_score"], reverse=True)
        findings = group_adjacent_findings(all_findings[:200])
        # Các ID nhóm passage chỉ dành cho UI, không phải kết luận đạo văn.
        if settings.classifier_enabled:
            from app.classifier import load_classifier

            _, classifier_manifest = load_classifier()
            classifier_version = classifier_manifest["model_version"]
        else:
            classifier_version = None
        report = {
            "schema_version": 2,
            "job_id": job_id,
            "corpus_id": job["corpus_id"],
            "submission_id": job["document_key"],
            "submission_version": job["version"],
            "model": {
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
                "classifier_version": classifier_version,
                "detector_mode": (
                    "classifier" if settings.classifier_enabled else "heuristic"
                ),
            },
            "summary": {
                "scanned_chunks": len(query_chunks),
                "flagged_pairs": len(all_findings),
                "findings_returned": len(findings),
                "matched_sources": len({f["source_key"] for f in all_findings}),
                "passage_groups": len({f["passage_group_id"] for f in findings}),
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
                SET status='completed', report=%s, updated_at=now()
                WHERE id=%s::uuid
                """,
                (Jsonb(report), job_id),
            )
    except InferenceError as exc:
        logger.error(
            "Scan job %s failed due to inference error (%s): %s",
            job_id,
            exc.error_code,
            exc,
        )
        set_status(job_id, "failed", exc.error_code)
    except Exception:
        logger.exception("Scan job failed: %s", job_id)
        set_status(job_id, "failed", "SCAN_ERROR")
