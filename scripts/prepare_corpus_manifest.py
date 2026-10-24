"""Inspect approved PDF/DOCX sources and create a sanitized staging corpus."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path

import pymupdf
from docx import Document


MANIFEST_FIELDS = (
    "source_key",
    "corpus_id",
    "version",
    "staged_filename",
    "sha256",
    "bytes",
    "page_count",
    "extracted_characters",
    "status",
    "reason",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_pdf(path: Path) -> tuple[int, int, str | None]:
    with path.open("rb") as source:
        if not source.read(5).startswith(b"%PDF-"):
            return 0, 0, "INVALID_PDF"
    try:
        with pymupdf.open(path) as document:
            page_count = len(document)
            text_length = sum(
                len(page.get_text("text", sort=True).strip()) for page in document
            )
    except Exception:
        return 0, 0, "UNREADABLE_PDF"
    return page_count, text_length, None


def validate_docx(path: Path) -> tuple[int, int, str | None]:
    if not zipfile.is_zipfile(path):
        return 0, 0, "INVALID_DOCX"
    try:
        with zipfile.ZipFile(path) as archive:
            if "word/document.xml" not in archive.namelist():
                return 0, 0, "INVALID_DOCX"
        document = Document(path)
        text_length = sum(len(paragraph.text.strip()) for paragraph in document.paragraphs)
    except Exception:
        return 0, 0, "UNREADABLE_DOCX"
    return 0, text_length, None


def inspect_file(path: Path, max_upload_bytes: int) -> tuple[int, int, str, str]:
    if path.stat().st_size > max_upload_bytes:
        return 0, 0, "needs_review", "FILE_TOO_LARGE"
    if path.suffix.lower() == ".pdf":
        page_count, text_length, error = validate_pdf(path)
    elif path.suffix.lower() == ".docx":
        page_count, text_length, error = validate_docx(path)
    else:
        return 0, 0, "needs_review", "UNSUPPORTED_EXTENSION"

    if error:
        return page_count, text_length, "needs_review", error
    if page_count > 250:
        return page_count, text_length, "needs_review", "TOO_MANY_PAGES"
    if text_length == 0:
        return page_count, text_length, "needs_ocr", "EMPTY_TEXT"
    return page_count, text_length, "ready", ""


def copy_to_staging(source: Path, destination: Path) -> None:
    if destination.exists():
        if sha256(destination) != sha256(source):
            raise RuntimeError(f"Staging collision for {destination.name}")
        return
    shutil.copy2(source, destination)


def build_manifest(
    input_dir: Path,
    staging_dir: Path,
    manifest_path: Path,
    corpus_id: str,
    version: int,
    key_prefix: str,
    max_upload_mb: int,
) -> Counter:
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input directory does not exist: {input_dir}")
    if version < 1:
        raise ValueError("version must be positive")

    staging_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    max_upload_bytes = max_upload_mb * 1024 * 1024
    seen_hashes: dict[str, str] = {}
    rows: list[dict[str, str | int]] = []

    candidates = sorted(
        path
        for path in input_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".pdf", ".docx"}
    )
    for path in candidates:
        file_hash = sha256(path)
        source_key = f"{key_prefix}-{file_hash[:16]}"
        suffix = path.suffix.lower()
        staged_filename = f"{source_key}{suffix}"
        page_count, text_length, status, reason = inspect_file(
            path, max_upload_bytes
        )
        if file_hash in seen_hashes:
            status = "duplicate"
            reason = f"DUPLICATE_OF:{seen_hashes[file_hash]}"
        else:
            seen_hashes[file_hash] = source_key
            if status == "ready":
                copy_to_staging(path, staging_dir / staged_filename)

        rows.append(
            {
                "source_key": source_key,
                "corpus_id": corpus_id,
                "version": version,
                "staged_filename": staged_filename,
                "sha256": file_hash,
                "bytes": path.stat().st_size,
                "page_count": page_count or "",
                "extracted_characters": text_length,
                "status": status,
                "reason": reason,
            }
        )

    with manifest_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return Counter(row["status"] for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--corpus-id", default="uit-library-v1")
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--key-prefix", default="kltn")
    parser.add_argument("--max-upload-mb", type=int, default=20)
    args = parser.parse_args()

    summary = build_manifest(
        input_dir=args.input_dir,
        staging_dir=args.staging_dir,
        manifest_path=args.manifest_out,
        corpus_id=args.corpus_id,
        version=args.version,
        key_prefix=args.key_prefix,
        max_upload_mb=args.max_upload_mb,
    )
    print(
        json.dumps(
            {
                "manifest": str(args.manifest_out),
                "staging": str(args.staging_dir),
                "summary": dict(summary),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
