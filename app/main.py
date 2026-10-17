import hashlib
import re
import secrets
import uuid
import zipfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

from app.config import settings
from app.db import connect, get_job
from app.tasks import index_source, scan_document

app = FastAPI(title="UGTM Plagiarism AI", version="0.1.0")
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


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

