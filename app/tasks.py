import logging

from psycopg.types.json import Jsonb

from app.alignment import align_passage_pair, group_adjacent_findings
from app.celery_app import celery_app
from app.config import settings
from app.db import (
    connect,
    get_job,
    record_job_checkpoint,
    record_job_heartbeat,
    schedule_waiting_for_compute,
    set_status,
    transition_job_status,
)
from app.features import build_features

logger = logging.getLogger(__name__)


class StorageTransientError(Exception):
    """Transient storage download failure (network glitch, timeout) eligible for retry."""
    pass


def parse_job(job):
    import requests
    from pathlib import Path
    from app.preprocessing import prepare_document_chunks
    from app.storage import (
        StorageError,
        StorageFileNotFoundError,
        download_to_temp_file,
    )

    try:
        with download_to_temp_file(
            job["file_path"],
            expected_sha256=job.get("file_sha256"),
            suffix=Path(job.get("filename", "")).suffix,
        ) as temp_path:
            if temp_path.stat().st_size == 0:
                raise ValueError("EMPTY_TEXT")
            try:
                chunks, stats = prepare_document_chunks(str(temp_path))
            except ValueError:
                raise
            except Exception as exc:
                logger.warning(
                    "Document parsing failed for file %s: %s",
                    job.get("filename"),
                    exc,
                )
                raise ValueError("CORRUPTED_DOCUMENT") from exc

            if not chunks:
                raise ValueError("EMPTY_TEXT")
            logger.info(
                "Parsed job %s: %d chunks generated (kept_blocks=%d, dropped_blocks=%d)",
                job.get("id"),
                len(chunks),
                stats.kept_blocks,
                stats.dropped_blocks,
            )
            return chunks
    except StorageFileNotFoundError as exc:
        logger.warning("Job %s file not found in storage: %s", job.get("id"), exc)
        raise ValueError("STORAGE_FILE_NOT_FOUND") from exc
    except StorageError as exc:
        if getattr(exc, "code", None) == "FILE_CHECKSUM_MISMATCH":
            logger.warning("Job %s checksum mismatch: %s", job.get("id"), exc)
            raise ValueError("FILE_CHECKSUM_MISMATCH") from exc
        logger.warning("Job %s storage error: %s", job.get("id"), exc)
        raise ValueError("STORAGE_ERROR") from exc
    except requests.RequestException as exc:
        logger.warning("Job %s transient network error downloading file: %s", job.get("id"), exc)
        raise StorageTransientError(f"Transient download error: {exc}") from exc


@celery_app.task(name="app.tasks.index_source")
def index_source(job_id: str):
    import os
    from app.inference import InferenceError
    from app.models import encode_texts
    from app.observability import StageTimer
    from app.search import vector_literal

    timer = StageTimer(job_id=job_id)

    job = get_job(job_id)
    if not job:
        logger.warning("Index job %s not found", job_id)
        return
    if job.get("status") == "cancelled":
        logger.info("Index job %s was cancelled; skipping execution.", job_id)
        return

    job_attempt = job.get("attempt") or 1
    worker_id = f"worker-{os.getpid()}"

    transition_job_status(
        job_id,
        "processing",
        from_status=["queued", "waiting_for_compute", "processing"],
        attempt=job_attempt,
        worker_id=worker_id,
    )
    record_job_heartbeat(job_id, worker_id)

    try:
        try:
            with timer.time_stage("parse"):
                chunks = parse_job(job)
        except StorageTransientError as exc:
            logger.warning(
                "Index job %s transient storage error during download: %s", job_id, exc
            )
            sched = schedule_waiting_for_compute(
                job_id,
                error_code="STORAGE_TRANSIENT_ERROR",
                checkpoint={"phase": "download_failed"},
            )
            if sched.get("action") == "retry":
                index_source.apply_async(
                    args=[job_id], countdown=int(sched.get("backoff_seconds", 10))
                )
            return

        record_job_checkpoint(job_id, "parsed", {"chunks_count": len(chunks)})

        try:
            with timer.time_stage("encode", {"chunks_count": len(chunks)}):
                embeddings = encode_texts([x.text for x in chunks])
        except InferenceError as exc:
            logger.warning(
                "Index job %s GPU inference failed during encoding (%s): %s",
                job_id,
                getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                exc,
            )
            sched = schedule_waiting_for_compute(
                job_id,
                error_code=getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                checkpoint={"phase": "parsed", "chunks_count": len(chunks)},
            )
            if sched.get("action") == "retry":
                index_source.apply_async(
                    args=[job_id], countdown=int(sched.get("backoff_seconds", 10))
                )
            return

        with timer.time_stage("index_db"):
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
            # Active versioning: kiểm tra phiên bản active hiện tại trong corpus
            active_row = con.execute(
                """
                SELECT id, version FROM integrity.sources
                WHERE corpus_id = %s AND source_key = %s AND is_active = TRUE
                """,
                (job["corpus_id"], job["document_key"]),
            ).fetchone()

            current_active_version = int(active_row["version"]) if active_row else 0
            job_version = int(job["version"])

            if job_version >= current_active_version:
                # Phiên bản này mới hơn hoặc bằng bản đang active: kích hoạt nó
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
                logger.info(
                    "Source %s v%d activated (previous active: v%d)",
                    job["document_key"],
                    job_version,
                    current_active_version,
                )
            else:
                # Phiên bản cũ hơn: index thành công nhưng không kích hoạt đè lên bản mới hơn
                con.execute(
                    """
                    UPDATE integrity.sources
                    SET is_active = FALSE, indexed_at = now()
                    WHERE id = %s
                    """,
                    (source_id,),
                )
                logger.info(
                    "Source %s v%d indexed as inactive (newer v%d already active)",
                    job["document_key"],
                    job_version,
                    current_active_version,
                )
                res = con.execute(
                    """
                    UPDATE integrity.jobs
                    SET status = 'completed', updated_at = now(),
                        last_heartbeat = now(), report = %s
                    WHERE id = %s::uuid AND status = 'processing' AND attempt = %s
                    """,
                    (
                        Jsonb(
                            {
                                "indexed_chunks": len(chunks),
                                "source_id": job["document_key"],
                                "is_active": job_version >= current_active_version,
                                "execution_telemetry": timer.to_telemetry_dict(),
                            }
                        ),
                        job_id,
                        job_attempt,
                    ),
                )
            if res.rowcount == 0:
                logger.warning(
                    "Index job %s: completion update skipped (attempt %s superseded or job cancelled).",
                    job_id,
                    job_attempt,
                )
    except ValueError as exc:
        code = str(exc)
        known_codes = {
            "CORPUS_EMPTY",
            "EMPTY_TEXT",
            "TOO_MANY_PAGES",
            "DOCUMENT_TOO_LONG",
            "CORRUPTED_DOCUMENT",
            "UNSUPPORTED_FILE_FORMAT",
            "STORAGE_FILE_NOT_FOUND",
            "FILE_CHECKSUM_MISMATCH",
            "STORAGE_ERROR",
        }
        err_code = code if code in known_codes else "INDEX_ERROR"
        logger.warning("Index job %s validation failed (%s): %s", job_id, err_code, exc)
        set_status(job_id, "failed", err_code)
    except Exception:
        logger.exception("Index job failed: %s", job_id)
        set_status(job_id, "failed", "INDEX_ERROR")


@celery_app.task(name="app.tasks.scan_document")
def scan_document(job_id: str):
    import os
    from app.classifier import score_candidate
    from app.inference import InferenceError
    from app.models import encode_texts, rerank_pairs
    from app.observability import StageTimer
    from app.search import load_retrieval_config, retrieve

    timer = StageTimer(job_id=job_id)

    job = get_job(job_id)
    if not job:
        logger.warning("Scan job %s not found", job_id)
        return
    if job.get("status") == "cancelled":
        logger.info("Scan job %s was cancelled; skipping execution.", job_id)
        return

    job_attempt = job.get("attempt") or 1
    worker_id = f"worker-{os.getpid()}"

    transition_job_status(
        job_id,
        "processing",
        from_status=["queued", "waiting_for_compute", "processing"],
        attempt=job_attempt,
        worker_id=worker_id,
    )
    record_job_heartbeat(job_id, worker_id)

    try:
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

        try:
            with timer.time_stage("parse"):
                query_chunks = parse_job(job)
        except StorageTransientError as exc:
            logger.warning(
                "Scan job %s transient storage error during download: %s", job_id, exc
            )
            sched = schedule_waiting_for_compute(
                job_id,
                error_code="STORAGE_TRANSIENT_ERROR",
                checkpoint={"phase": "download_failed"},
            )
            if sched.get("action") == "retry":
                scan_document.apply_async(
                    args=[job_id], countdown=int(sched.get("backoff_seconds", 10))
                )
            return

        record_job_checkpoint(job_id, "parsed", {"chunks_count": len(query_chunks)})

        try:
            with timer.time_stage("encode", {"chunks_count": len(query_chunks)}):
                vectors = encode_texts([x.text for x in query_chunks])
        except InferenceError as exc:
            logger.warning(
                "Scan job %s GPU inference failed during encoding (%s): %s",
                job_id,
                getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                exc,
            )
            sched = schedule_waiting_for_compute(
                job_id,
                error_code=getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                checkpoint={"phase": "parsed", "chunks_count": len(query_chunks)},
            )
            if sched.get("action") == "retry":
                scan_document.apply_async(
                    args=[job_id], countdown=int(sched.get("backoff_seconds", 10))
                )
            return

        all_findings: list[dict] = []
        exclude_source_keys = [job["document_key"]] if job.get("document_key") else None
        exclude_document_family = job.get("document_family")

        # Lựa chọn ứng viên theo configs/retrieval_config.json (CP-04.1)
        retrieval_cfg = load_retrieval_config()
        prefilter_cfg = retrieval_cfg.get("prefilter", {})
        prefilter_enabled = prefilter_cfg.get("enabled", False)
        min_dense = float(prefilter_cfg.get("min_dense_score", 0.48))
        min_lex = float(prefilter_cfg.get("min_lexical_score", 0.25))

        candidates_to_rerank: list[tuple] = []
        with timer.time_stage("retrieve"):
            for query_chunk, vector in zip(query_chunks, vectors):
                candidates = retrieve(
                    query_chunk.text,
                    vector,
                    job["corpus_id"],
                    exclude_source_keys=exclude_source_keys,
                    exclude_document_family=exclude_document_family,
                )
                if not candidates:
                    continue
                for item in candidates:
                    dense = float(item["dense_score"])
                    lexical = float(item["lexical_score"])
                    # Khi prefilter được bật, chỉ giữ các đoạn thỏa mãn ngưỡng thô
                    if not prefilter_enabled or (dense >= min_dense or lexical >= min_lex):
                        candidates_to_rerank.append((query_chunk, item))

        record_job_checkpoint(
            job_id, "retrieved", {"candidates_count": len(candidates_to_rerank)}
        )
        record_job_heartbeat(job_id, worker_id)

        if candidates_to_rerank:
            pairs = [
                [chunk.text, item["source_text"]]
                for chunk, item in candidates_to_rerank
            ]
            try:
                with timer.time_stage("rerank", {"pairs_count": len(candidates_to_rerank)}):
                    scores = rerank_pairs(pairs, batch_size=16)
            except InferenceError as exc:
                logger.warning(
                    "Scan job %s GPU inference failed during reranking (%s): %s",
                    job_id,
                    getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                    exc,
                )
                sched = schedule_waiting_for_compute(
                    job_id,
                    error_code=getattr(exc, "error_code", "GPU_UNAVAILABLE"),
                    checkpoint={"phase": "retrieved", "candidates_count": len(candidates_to_rerank)},
                )
                if sched.get("action") == "retry":
                    scan_document.apply_async(
                        args=[job_id], countdown=int(sched.get("backoff_seconds", 10))
                    )
                return

            with timer.time_stage("align_and_citation"):
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
                            or result.get("needs_human_review", False)
                        )
                        decision_source = (
                            "near_exact_override"
                            if lexical >= 0.92
                            else result.get("decision_source", "custom_classifier")
                        )
                    if not review:
                        continue
                    from app.citation import detect_citation_context

                    cit_ctx = detect_citation_context(
                        query_text=query_chunk.text,
                        source_metadata={
                            "source_key": str(item.get("source_key", "")),
                            "source_version": int(item.get("source_version", 1)),
                            "source_filename": str(item.get("source_filename", "")),
                            "document_family": str(item.get("document_family", "")),
                        },
                    )
                    spans = align_passage_pair(
                        query_text=query_chunk.text,
                        source_text=item.get("source_text", ""),
                        query_base_offset=query_chunk.start_offset,
                        source_base_offset=int(item.get("source_start", 0)),
                        source_key=str(item.get("source_key", "")),
                        source_version=int(item.get("source_version", 1)),
                        source_filename=str(item.get("source_filename", "")),
                    )
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
                            "citation_status": cit_ctx.status,
                            "citation_context": cit_ctx.to_dict(),
                            "feature_values": features,
                            "review_required": True,
                            "aligned_spans": [s.to_dict() for s in spans] if spans else None,
                        }
                    )

        if settings.classifier_enabled:
            from app.classifier import load_classifier

            _, classifier_manifest = load_classifier()
            classifier_version = classifier_manifest["model_version"]
            detector_mode = "custom_classifier"
        else:
            classifier_version = None
            detector_mode = "heuristic"

        from app.reports import build_report_v3

        with timer.time_stage("report_generation"):
            report = build_report_v3(
                job_id=job_id,
                job=job,
                query_chunks=query_chunks,
                all_findings=all_findings,
                preprocessing_profile="production_v1",
                corpus_snapshot_id="corpus-v2-73fams",
                classifier_version=classifier_version,
                detector_mode=detector_mode,
                execution_telemetry=timer.to_telemetry_dict(),
            )
        with connect() as con:
            res = con.execute(
                """
                UPDATE integrity.jobs
                SET status = 'completed', report = %s, updated_at = now(), last_heartbeat = now()
                WHERE id = %s::uuid AND status = 'processing' AND attempt = %s
                """,
                (Jsonb(report), job_id, job_attempt),
            )
            if res.rowcount == 0:
                logger.warning(
                    "Scan job %s: completion update skipped (attempt %s superseded or job cancelled).",
                    job_id,
                    job_attempt,
                )
    except ValueError as exc:
        code = str(exc)
        known_codes = {
            "CORPUS_EMPTY",
            "EMPTY_TEXT",
            "TOO_MANY_PAGES",
            "DOCUMENT_TOO_LONG",
            "CORRUPTED_DOCUMENT",
            "UNSUPPORTED_FILE_FORMAT",
            "STORAGE_FILE_NOT_FOUND",
            "FILE_CHECKSUM_MISMATCH",
            "STORAGE_ERROR",
        }
        err_code = code if code in known_codes else "SCAN_ERROR"
        logger.warning("Scan job %s validation failed (%s): %s", job_id, err_code, exc)
        set_status(job_id, "failed", err_code)
    except Exception:
        logger.exception("Scan job failed: %s", job_id)
        set_status(job_id, "failed", "SCAN_ERROR")
