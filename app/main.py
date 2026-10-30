import hashlib
import json
import logging
import re
import secrets
import uuid
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db import connect, get_job
from app.tasks import index_source, scan_document

logger = logging.getLogger(__name__)

app = FastAPI(title="UGTM Plagiarism AI", version="0.1.0")
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
STATIC_DIR = Path(__file__).resolve().parent / "static"

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")):
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def validate_metadata(corpus_id: str, document_key: str, version: int):
    if (
        not SAFE_KEY.fullmatch(corpus_id)
        or not SAFE_KEY.fullmatch(document_key)
        or version < 1
    ):
        raise HTTPException(status_code=422, detail="Invalid metadata")


async def save_upload(file: UploadFile) -> tuple[str, str, str]:
    filename = Path(file.filename or "unnamed").name[:180]
    extension = Path(filename).suffix.lower()
    if extension not in {".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="Only PDF/DOCX allowed")

    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{uuid.uuid4().hex}{extension}"
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with path.open("wb") as destination:
            while data := await file.read(1024 * 1024):
                byte_count += len(data)
                if byte_count > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="File too large")
                digest.update(data)
                destination.write(data)

        if byte_count == 0:
            raise HTTPException(status_code=400, detail="Empty file")

        if extension == ".pdf":
            with path.open("rb") as source:
                if not source.read(5).startswith(b"%PDF-"):
                    raise HTTPException(status_code=415, detail="Invalid PDF")
        else:
            if not zipfile.is_zipfile(path):
                raise HTTPException(status_code=415, detail="Invalid DOCX")
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise HTTPException(status_code=415, detail="Invalid DOCX")
                expanded_size = sum(info.file_size for info in archive.infolist())
                if expanded_size > 100 * 1024 * 1024:
                    raise HTTPException(
                        status_code=413,
                        detail="Expanded DOCX too large",
                    )

        return str(path), digest.hexdigest(), filename
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def insert_job(
    job_type: str,
    corpus_id: str,
    document_key: str,
    version: int,
    filename: str,
    path: str,
    sha: str,
):
    job_id = str(uuid.uuid4())
    with connect() as con:
        created = con.execute(
            """
            INSERT INTO integrity.jobs
              (id, job_type, corpus_id, document_key, version,
               filename, file_path, file_sha256)
            VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_type, corpus_id, document_key, version)
            DO NOTHING RETURNING id
            """,
            (
                job_id,
                job_type,
                corpus_id,
                document_key,
                version,
                filename,
                path,
                sha,
            ),
        ).fetchone()
        if created:
            return job_id, True

        existing = con.execute(
            """
            SELECT id, file_sha256 FROM integrity.jobs
            WHERE job_type = %s AND corpus_id = %s AND document_key = %s
              AND version = %s
            """,
            (job_type, corpus_id, document_key, version),
        ).fetchone()
        if existing["file_sha256"] != sha:
            raise HTTPException(
                status_code=409,
                detail="Version already exists with different content",
            )
        return str(existing["id"]), False


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index_page():
    if FRONTEND_DIST.exists():
        index_file = FRONTEND_DIST / "index.html"
        if index_file.exists():
            return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UGTM Plagiarism AI is running.</h1><p>Static index.html not found.</p>")


@app.get("/api/v1/client-config")
def client_config():
    """Cung cấp cấu hình cho Web Dashboard khi chạy nội bộ."""
    return {
        "api_key": settings.api_key,
        "default_corpus": "uit_thesis",
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
        "classifier_enabled": settings.classifier_enabled,
        "max_upload_mb": settings.max_upload_mb,
        "inference_mode": settings.inference_mode,
        "remote_gpu_url": settings.remote_gpu_url,
        "remote_gpu_enabled": bool(settings.remote_gpu_url),
    }


@app.get("/api/v1/jobs", dependencies=[Depends(require_key)])
def list_jobs(limit: int = 50, job_type: str | None = None):
    with connect() as con:
        if job_type:
            rows = con.execute(
                """
                SELECT id, job_type, corpus_id, document_key, version,
                       filename, status, error_code, created_at, updated_at
                FROM integrity.jobs
                WHERE job_type = %s
                ORDER BY created_at DESC LIMIT %s
                """,
                (job_type, limit),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT id, job_type, corpus_id, document_key, version,
                       filename, status, error_code, created_at, updated_at
                FROM integrity.jobs
                ORDER BY created_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "job_id": str(r["id"]),
                "job_type": r["job_type"],
                "corpus_id": r["corpus_id"],
                "document_key": r["document_key"],
                "version": r["version"],
                "filename": r["filename"],
                "status": r["status"],
                "error_code": r["error_code"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
            }
            for r in rows
        ]


@app.get("/api/v1/corpus/documents", dependencies=[Depends(require_key)])
def list_sources(corpus_id: str = "uit_thesis"):
    with connect() as con:
        rows = con.execute(
            """
            SELECT s.id, s.corpus_id, s.source_key, s.version, s.filename,
                   s.is_active, s.indexed_at,
                   COUNT(c.id) AS chunk_count
            FROM integrity.sources s
            LEFT JOIN integrity.chunks c ON c.source_id = s.id
            WHERE s.corpus_id = %s
            GROUP BY s.id, s.corpus_id, s.source_key, s.version, s.filename,
                     s.is_active, s.indexed_at
            ORDER BY s.indexed_at DESC NULLS LAST
            """,
            (corpus_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "corpus_id": r["corpus_id"],
                "source_key": r["source_key"],
                "version": r["version"],
                "filename": r["filename"],
                "is_active": r["is_active"],
                "chunk_count": r["chunk_count"],
                "indexed_at": r["indexed_at"].isoformat() if r["indexed_at"] else None,
            }
            for r in rows
        ]


@app.post(
    "/api/v1/corpus/documents",
    status_code=202,
    dependencies=[Depends(require_key)],
)
async def add_source(
    corpus_id: str = Form(...),
    source_id: str = Form(...),
    version: int = Form(1),
    file: UploadFile = File(...),
):
    validate_metadata(corpus_id, source_id, version)
    path, sha, filename = await save_upload(file)
    try:
        job_id, created = insert_job(
            "index",
            corpus_id,
            source_id,
            version,
            filename,
            path,
            sha,
        )
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise

    if created:
        index_source.delay(job_id)
    else:
        Path(path).unlink(missing_ok=True)
    return {"job_id": job_id, "status": "queued" if created else "existing"}


@app.post(
    "/api/v1/scans",
    status_code=202,
    dependencies=[Depends(require_key)],
)
async def start_scan(
    corpus_id: str = Form(...),
    submission_id: str = Form(...),
    version: int = Form(1),
    file: UploadFile = File(...),
):
    validate_metadata(corpus_id, submission_id, version)
    path, sha, filename = await save_upload(file)
    try:
        job_id, created = insert_job(
            "scan",
            corpus_id,
            submission_id,
            version,
            filename,
            path,
            sha,
        )
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise

    if created:
        scan_document.delay(job_id)
    else:
        Path(path).unlink(missing_ok=True)
    return {"job_id": job_id, "status": "queued" if created else "existing"}


@app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_key)])
def job_status(job_id: uuid.UUID):
    job = get_job(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": str(job["id"]),
        "type": job["job_type"],
        "status": job["status"],
        "error_code": job["error_code"],
        "updated_at": job["updated_at"],
    }


@app.get("/api/v1/reports/{job_id}", dependencies=[Depends(require_key)])
def report(job_id: uuid.UUID):
    job = get_job(str(job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "completed":
        raise HTTPException(status_code=409, detail="Job not completed")
    return job["report"] or {}


@app.post("/api/v1/demo/quick-scan", status_code=202, dependencies=[Depends(require_key)])
async def demo_quick_scan():
    """Nộp nhanh bài mẫu nghi vấn suspect_doc.docx để quét đạo văn thử nghiệm."""
    sample_path = STATIC_DIR / "samples" / "suspect_doc.docx"
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample suspect_doc.docx not found")

    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"{uuid.uuid4().hex}.docx"

    file_bytes = sample_path.read_bytes()
    target_path.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()

    submission_id = f"kltn_nop_{uuid.uuid4().hex[:6]}"
    job_id, created = insert_job(
        "scan",
        "uit_thesis",
        submission_id,
        1,
        "suspect_doc.docx",
        str(target_path),
        sha,
    )
    if created:
        scan_document.delay(job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "submission_id": submission_id,
        "filename": "suspect_doc.docx",
    }


@app.post("/api/v1/demo/quick-index", status_code=202, dependencies=[Depends(require_key)])
async def demo_quick_index():
    """Nạp nhanh bài mẫu nguồn source_doc.docx vào kho luận văn để thử nghiệm."""
    sample_path = STATIC_DIR / "samples" / "source_doc.docx"
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail="Sample source_doc.docx not found")

    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    target_path = upload_dir / f"{uuid.uuid4().hex}.docx"

    file_bytes = sample_path.read_bytes()
    target_path.write_bytes(file_bytes)
    sha = hashlib.sha256(file_bytes).hexdigest()

    source_id = f"kltn_nguon_{uuid.uuid4().hex[:6]}"
    job_id, created = insert_job(
        "index",
        "uit_thesis",
        source_id,
        1,
        "source_doc.docx",
        str(target_path),
        sha,
    )
    if created:
        index_source.delay(job_id)
    return {
        "job_id": job_id,
        "status": "queued",
        "source_id": source_id,
        "filename": "source_doc.docx",
    }
