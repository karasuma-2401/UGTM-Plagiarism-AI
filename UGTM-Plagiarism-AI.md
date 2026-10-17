# UGTM-Plagiarism-AI — Tài liệu thiết kế và triển khai

> **Mục đích:** Xây dựng một dịch vụ AI kiểm tra **mức độ tương đồng học thuật** cho hệ thống quản lý khóa luận tốt nghiệp UGTM, không làm nghiên cứu khoa học và không tự động kết luận sinh viên đạo văn.
>
> **Giả định đã chốt (17/10/2026):** Nhóm có GPU NVIDIA chạy được PyTorch/CUDA; tạo **repo Python riêng**; hệ thống UGTM tiếp tục sử dụng **ASP.NET Core (.NET 10), React 19, PostgreSQL**, kiến trúc Modular Monolith/DDD; dự định xin phép thư viện UIT sử dụng khoảng **20–50 khóa luận** làm nguồn đối chiếu.
>
> **Tình trạng kiểm chứng:** Kết nối GitHub tới `karasuma-2401/UGTM` trả về 404, nên **chưa review được mã nguồn thực tế**. Các tên class/module tích hợp .NET trong tài liệu là **đề xuất**, không phải tên hiện có của repo. Những đặc điểm .NET/DDD/PDF-DOCX căn cứ vào báo cáo phân tích thiết kế của nhóm. Code bên dưới là **reference implementation hướng MVP**, chưa được chạy end-to-end trên GPU, Docker hay repo của nhóm; cần kiểm thử tại môi trường thật trước khi dùng chính thức.

## 0. Quyết định kỹ thuật và phạm vi

### Chốt kiến trúc

- **Repo 1** — `karasuma-2401/UGTM`: giao diện, đăng nhập, phân quyền, quy trình nộp báo cáo, giữ file gốc, liên kết kết quả AI với bản nộp, cho GVHD/GVPB xem báo cáo.
- **Repo 2** — `UGTM-Plagiarism-AI`: dịch vụ Python **FastAPI**, **Celery worker** dùng GPU, trích xuất PDF/DOCX, tìm kiếm lexical + semantic, reranking, trả bằng chứng đối chiếu.
- **Database:** PostgreSQL có `pgvector`, dùng schema `integrity`; bản MVP có thể sử dụng **database riêng** `ugtm_ai` để tránh phụ thuộc bảng nghiệp vụ của UGTM. Không chia sẻ Entity Framework DbContext hoặc domain entity qua hai dịch vụ.
- **Queue:** Redis + Celery, không chạy AI trực tiếp trong HTTP request của sinh viên.
- **Model:** `BAAI/bge-m3` (**dense 1024 chiều**) và `BAAI/bge-reranker-v2-m3`, chạy inference GPU, không fine-tune trong MVP.
- **Lexical:** PostgreSQL `pg_trgm` trên các đoạn văn, đủ cho corpus nhỏ. Sau này mới cân nhắc BM25/Elasticsearch/OpenSearch.
- **Quyền:** sinh viên **không** gọi AI trực tiếp; ASP.NET Core gọi API nội bộ có xác thực service-to-service. Báo cáo là gợi ý để giảng viên kiểm tra.

### MVP phải có

1. Admin/Khoa nhập khóa luận nguồn `.pdf`/`.docx` vào corpus `uit-library-v1` qua API nội bộ.
2. Tách văn bản thành chunks, gắn `source_id`, phiên bản, trang, khoảng ký tự; tạo embedding và lưu pgvector.
3. Sinh viên nộp bài trên UGTM; .NET tạo job quét và trả trạng thái `queued` ngay.
4. Worker thực hiện lexical search + dense vector search + RRF + reranker.
5. Báo cáo gồm **đoạn trong bài nộp**, **đoạn nguồn**, tài liệu, trang, điểm lexical/dense/reranker, cờ `review_required`.
6. GVHD/GVPB có màn hình xem và nhận xét; AI không tự cấm nộp, không tự đánh rớt KLTN.

### Những thứ **không** tuyên bố đã có trong code MVP

- OCR PDF scan; PDF nhiều cột/bảng/hình ảnh phức tạp; đọc mã nguồn và biểu đồ; phát hiện đạo văn ý tưởng.
- Phân tích trích dẫn đáng tin cậy ở cấp câu; phân loại “nội dung AI tạo ra”; tìm kiếm trên toàn Internet.
- Highlight theo **tọa độ PDF**; code chỉ có **offset trên văn bản đã trích xuất** và `page_number` PDF. DOCX **không** có trang ổn định.
- Độ chính xác hay ngưỡng “đạo văn 30%”; muốn có cần dữ liệu gán nhãn và hiệu chỉnh.
- Vận hành production: object storage, retry/dead-letter, metrics, migration/backup đầy đủ và bảo vệ dữ liệu nâng cao được liệt kê ở cuối tài liệu.

## 1. Kiến trúc tổng thể

```text
                 REPOSITORY: UGTM (.NET + React)
  Sinh viên --> React --> ASP.NET Core --> UGTM file storage
                            |
                  SubmissionAccepted event
                            |
                       IntegrityAdapter
                            | multipart + X-API-Key
                            v
              REPOSITORY: UGTM-Plagiarism-AI
  +-------------------------------------------------------+
  |  FastAPI (chỉ điều phối, không load AI model)         |
  |    POST corpus/documents --> job index                 |
  |    POST scans -----------> job scan                    |
  |    GET jobs/{id}, reports/{id}                         |
  +---------------------------+---------------------------+
                              |
                          Celery / Redis
                              |
                              v
  +-------------------------------------------------------+
  | GPU Worker (concurrency=1 lúc đầu)                    |
  | extract -> chunk -> BGE-M3 -> lexical/vector retrieve |
  |                         -> RRF -> BGE reranker        |
  |                         -> evidence/report            |
  +---------------------------+---------------------------+
                              |
                PostgreSQL + pgvector + pg_trgm
                  sources / chunks / jobs
```

**Hai pipeline riêng biệt:**

- **Indexing (ít chạy):** khóa luận nguồn → text extraction → chunking → embedding → lưu vector + metadata → bật `is_active` cho phiên bản mới.
- **Scanning (mỗi bản nộp):** báo cáo mới → extraction → chunking → embedding → lấy top-K từ corpus đang active → tìm kiếm lexical → RRF → rerank → kết quả.

## 2. Cách hoạt động của các thuật toán

**Chunking:** MVP tách theo từng trang PDF, dùng cửa sổ **90 từ trắng / chồng lấn 20 từ** (đây là *word windows*, **không phải** 90 model tokens). `max_length=512` trong BGE-M3 để giảm VRAM. Tăng chất lượng bằng cách tách theo đoạn/mục và tokenizer thực ở giai đoạn sau.

**Dense similarity:** BGE-M3 sinh vector; Postgres tìm nearest neighbors với cosine distance (`<=>`). `dense_score = 1 - cosine_distance`.

**Lexical similarity:** `pg_trgm.similarity(content, query)` giúp bắt sao chép hoặc thay đổi nhỏ; không đủ bắt paraphrase sâu.

**Fusion:** Reciprocal Rank Fusion (RRF) theo thứ hạng, không cộng trực tiếp hai thang điểm vốn không tương đương:

\[
\mathrm{RRF}(d)=\sum_{r\in\{\mathrm{dense,lexical}\}}\frac{1}{60+\mathrm{rank}_r(d)}.
\]

**Reranking:** `bge-reranker-v2-m3` chấm từng cặp (đoạn nộp, đoạn nguồn). `normalize=True` trả về score sau sigmoid **không phải xác suất đạo văn**. Chỉ rerank danh sách ngắn để giảm chi phí GPU.

**Cờ cần rà soát:** ví dụ MVP dùng `lexical >= 0.65` **hoặc** `(dense >= 0.68 và reranker >= 0.67)`. Đây **chỉ là ngưỡng khởi tạo để demo**, không phải ngưỡng khoa học hay quy chế UIT: phải điều chỉnh sau khi xem mẫu thật. Không tự động kết luận vi phạm.

**Không tính phần trăm đạo văn trong MVP.** Nếu thêm “tỷ lệ vùng văn bản trùng với kho”, phải gộp khoảng offset (tránh đếm double do chunk chồng lấn), xác định mẫu số là phần văn bản đủ điều kiện và giải thích phụ thuộc vào tập tài liệu đã index.

## 3. Tạo repo riêng

Tên gợi ý: `UGTM-Plagiarism-AI` (private repository nếu dùng dữ liệu trường).

```text
UGTM-Plagiarism-AI/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── db.py
│   ├── documents.py
│   ├── models.py
│   ├── search.py
│   ├── celery_app.py
│   ├── tasks.py
│   └── main.py
├── sql/
│   └── 001_init.sql
├── tests/
│   └── test_documents.py
├── requirements.txt
├── Dockerfile
├── compose.yaml
├── .env.example
├── .gitignore
└── README.md
```

**Lưu ý:** Tất cả nội dung các file mẫu kể trên đều được ghi **trong chính tài liệu này**. Khi lập repo, tạo đúng đường dẫn và copy code từ các khối tương ứng. Không commit PDF/DOCX khóa luận, model weights, `.env`, secrets, job output hoặc vector data lên GitHub.

### `.gitignore`

```gitignore
.env
.venv/
__pycache__/
.pytest_cache/
*.pyc
models/
data/
*.pdf
*.docx
*.sql.gz
*.log
```

`*.pdf` và `*.docx` ngăn commit nhầm dữ liệu thật; nếu muốn lưu fixtures test công khai, phải tạo ngoại lệ chính xác cho thư mục fixtures đã được phép sử dụng.

## 4. Cài đặt Docker/GPU

**Yêu cầu:** Linux server hoặc WSL2 có NVIDIA driver, Docker Engine/Compose Plugin và NVIDIA Container Toolkit; `nvidia-smi` hoạt động; `docker compose ...` có thể phân bổ GPU. Kiểm thử riêng GPU trước:

```bash
nvidia-smi
# Sau khi dựng worker:
docker compose exec worker python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

### `requirements.txt`

```txt
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
python-multipart>=0.0.12,<1
pydantic-settings>=2.5,<3
psycopg[binary]>=3.2,<4
celery[redis]>=5.4,<6
PyMuPDF>=1.24,<2
python-docx>=1.1,<2
FlagEmbedding>=1.3,<2
numpy>=1.26,<3
pytest>=8,<10
```

> PyTorch/CUDA có sẵn từ image nền. Các khoảng phiên bản là xuất phát điểm; **sau khi chạy thành công cần lock phiên bản/sha image** để tái lập được môi trường. Cập nhật phiên bản thư viện có thể làm thay đổi API hoặc phụ thuộc (`FlagEmbedding`/`transformers`).

### `Dockerfile`

```dockerfile
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime
WORKDIR /service
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### `.env.example`

```dotenv
POSTGRES_PASSWORD=replace-with-strong-password
AI_API_KEY=replace-with-long-random-private-key
MAX_UPLOAD_MB=20
```

Sau đó chạy `cp .env.example .env` và sửa bằng secret thật. Với việc triển khai máy chủ, cấp `.env` qua secret manager thay vì git.

### `compose.yaml`

```yaml
services:
  db:
    image: pgvector/pgvector:pg17
    environment:
      POSTGRES_DB: ugtm_ai
      POSTGRES_USER: ugtm_ai
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./sql/001_init.sql:/docker-entrypoint-initdb.d/001_init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ugtm_ai -d ugtm_ai"]
      interval: 5s
      retries: 20

  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      retries: 20

  api:
    build: .
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    ports:
      - "127.0.0.1:8000:8000"
    environment: &ai_env
      POSTGRES_DSN: postgresql://ugtm_ai:${POSTGRES_PASSWORD}@db:5432/ugtm_ai
      REDIS_URL: redis://redis:6379/0
      API_KEY: ${AI_API_KEY}
      MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-20}
      DATA_DIR: /data
      HF_HOME: /models
    volumes:
      - uploads:/data
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy

  worker:
    build: .
    command: celery -A app.celery_app.celery_app worker --loglevel=INFO --concurrency=1 --pool=solo
    environment: *ai_env
    volumes:
      - uploads:/data
      - models:/models
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

volumes:
  pgdata:
  uploads:
  models:
```

**Vận hành:** `api` chỉ CPU; `worker` độc quyền một GPU để tránh hai worker cùng tải hai model gây OOM. Vì Redis/DB không có `ports`, chúng không được expose ra host. Với UGTM chạy trên cùng Docker network, gọi tên service `http://api:8000`; nếu .NET chạy ngoài Docker, dùng endpoint internal có TLS/reverse proxy, không mở public trực tiếp port 8000.

**Lưu ý Postgres init script:** `docker-entrypoint-initdb.d` chỉ tự chạy khi volume `pgdata` **mới hoàn toàn**. Nếu database đã tồn tại, chạy migration SQL thủ công; không xóa volume có dữ liệu thật chỉ để cập nhật schema.

## 5. Database — `sql/001_init.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS integrity;

CREATE TABLE IF NOT EXISTS integrity.jobs (
    id UUID PRIMARY KEY,
    job_type TEXT NOT NULL CHECK (job_type IN ('index','scan')),
    corpus_id TEXT NOT NULL,
    document_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    filename TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','processing','completed','failed')),
    report JSONB,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(job_type, corpus_id, document_key, version)
);

CREATE TABLE IF NOT EXISTS integrity.sources (
    id BIGSERIAL PRIMARY KEY,
    corpus_id TEXT NOT NULL,
    source_key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    filename TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    indexed_at TIMESTAMPTZ,
    UNIQUE(corpus_id, source_key, version)
);

CREATE TABLE IF NOT EXISTS integrity.chunks (
    id BIGSERIAL PRIMARY KEY,
    source_id BIGINT NOT NULL REFERENCES integrity.sources(id) ON DELETE CASCADE,
    chunk_no INTEGER NOT NULL,
    page_number INTEGER,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1024) NOT NULL,
    UNIQUE(source_id, chunk_no)
);

CREATE INDEX IF NOT EXISTS idx_source_active
  ON integrity.sources (corpus_id, is_active);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON integrity.chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_trgm
  ON integrity.chunks USING GIN (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_jobs_created
  ON integrity.jobs (created_at DESC);

-- Chỉ tạo HNSW sau khi đo truy vấn chính xác thấy chậm.
-- CREATE INDEX idx_chunks_hnsw ON integrity.chunks
--   USING hnsw (embedding vector_cosine_ops);
```

**Giải thích:** `sources` và `chunks` giữ **chỉ các nguồn được thư viện cấp quyền**; mỗi nguồn có version và `is_active`. Khi version mới index thành công mới tắt version cũ trong **cùng một transaction**. Dữ liệu report lưu trong `jobs.report`. Với kho 20–50 tài liệu, truy vấn vector exact trước là lựa chọn hợp lý; HNSW là approximate và có thể ảnh hưởng Recall.

## 6. Code Python — cấu hình và database

### `app/__init__.py`

Tạo file rỗng.

### `app/config.py`

```python
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    postgres_dsn: str
    redis_url: str
    api_key: str
    max_upload_mb: int = 20
    data_dir: Path = Path("/data")
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"


settings = Settings()
```

### `app/db.py`

```python
import psycopg
from psycopg.rows import dict_row
from app.config import settings


def connect():
    return psycopg.connect(settings.postgres_dsn, row_factory=dict_row)


def set_status(job_id: str, status: str, error_code: str | None = None):
    with connect() as con:
        con.execute("""
            UPDATE integrity.jobs
            SET status = %s, error_code = %s, updated_at = now()
            WHERE id = %s::uuid
        """, (status, error_code, job_id))


def get_job(job_id: str):
    with connect() as con:
        return con.execute("""
            SELECT * FROM integrity.jobs WHERE id = %s::uuid
        """, (job_id,)).fetchone()
```

## 7. Code Python — trích xuất và chia đoạn

### `app/documents.py`

```python
import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document


@dataclass
class TextPage:
    page_number: int | None
    text: str


@dataclass
class Chunk:
    chunk_no: int
    page_number: int | None
    start_offset: int
    end_offset: int
    text: str


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_pages(path: str) -> list[TextPage]:
    file = Path(path)
    if file.suffix.lower() == ".pdf":
        with pymupdf.open(str(file)) as pdf:
            if len(pdf) > 250:
                raise ValueError("TOO_MANY_PAGES")
            return [
                TextPage(i + 1, normalize(page.get_text("text", sort=True)))
                for i, page in enumerate(pdf)
            ]
    if file.suffix.lower() == ".docx":
        doc = Document(str(file))
        # DOCX không có số trang cố định. MVP chỉ lấy paragraphs.
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if sum(len(p) for p in paragraphs) > 2_000_000:
            raise ValueError("DOCUMENT_TOO_LONG")
        return [TextPage(None, normalize("\n".join(paragraphs)))]
    raise ValueError("Only PDF and DOCX are supported")


def make_chunks(pages: list[TextPage], size: int = 90,
                overlap: int = 20, min_words: int = 8) -> list[Chunk]:
    if not (0 <= overlap < size):
        raise ValueError("overlap must be smaller than size")
    result: list[Chunk] = []
    chunk_no = 0
    for page in pages:
        # Offset tính trên văn bản đã normalize, không phải PDF bounding box.
        spans = list(re.finditer(r"\S+", page.text))
        for first in range(0, len(spans), size - overlap):
            last = min(first + size, len(spans))
            if last - first < min_words:
                break
            start = spans[first].start()
            end = spans[last - 1].end()
            result.append(Chunk(
                chunk_no=chunk_no,
                page_number=page.page_number,
                start_offset=start,
                end_offset=end,
                text=page.text[start:end],
            ))
            chunk_no += 1
            if last == len(spans):
                break
    return result
```

**Giới hạn extraction:** PDF scan chỉ chứa ảnh sẽ trả về văn bản rỗng hoặc không đầy đủ; cần báo `EMPTY_TEXT` và yêu cầu file có text layer. DOCX có bảng, hình, footnote phức tạp cần mở rộng parser. Muốn highlight theo vị trí PDF phải lưu `bbox` ở bước `page.get_text('dict')`, đối chiếu token-to-box và xử lý dòng/cột/ligature; *không* được dùng `start_offset` của normalized text làm tọa độ PDF.

## 8. Code Python — chạy model trên GPU

### `app/models.py`

```python
from functools import lru_cache

import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker
from app.config import settings


def assert_gpu():
    if not torch.cuda.is_available():
        raise RuntimeError("GPU CUDA is required by this configuration")


@lru_cache(maxsize=1)
def embedder():
    assert_gpu()
    return BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=True,
        devices=["cuda:0"],
    )


@lru_cache(maxsize=1)
def reranker():
    assert_gpu()
    return FlagReranker(
        settings.reranker_model,
        use_fp16=True,
        devices=["cuda:0"],
    )


def encode_texts(texts: list[str], batch_size: int = 8) -> np.ndarray:
    if not texts:
        return np.empty((0, 1024), dtype=np.float32)
    data = embedder().encode(
        texts,
        batch_size=batch_size,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return np.asarray(data["dense_vecs"], dtype=np.float32)


def rerank_pairs(pairs: list[list[str]], batch_size: int = 8) -> list[float]:
    if not pairs:
        return []
    scores = reranker().compute_score(
        pairs, batch_size=batch_size, normalize=True,
        max_length=512,
    )
    if isinstance(scores, (float, int)):
        return [float(scores)]
    return [float(v) for v in scores]
```

**GPU:** Model tải một lần khi worker gọi đầu tiên. Có thể mất vài phút trong lần đầu (download trọng số). Nếu VRAM không đủ, giảm `batch_size` 8 → 4 → 2, giảm số cặp rerank, hoặc chia embedding/rerank thành hai worker khác GPU. Sau khi ổn định cần lưu model snapshot revision và cache weights. GPU NVIDIA không đồng nghĩa chắc chắn có đủ VRAM cho cả hai model — phải đo bằng `nvidia-smi`.

## 9. Code Python — truy tìm ứng viên và hợp nhất RRF

### `app/search.py`

```python
import json
from app.db import connect


def vector_literal(vector) -> str:
    # Cast ::vector ở SQL; không dựng câu SQL bằng nối chuỗi.
    return json.dumps([float(x) for x in vector], separators=(",", ":"))


def retrieve(query_text: str, query_vector, corpus_id: str,
             top_k: int = 6, fused_k: int = 5) -> list[dict]:
    vector = vector_literal(query_vector)
    with connect() as con:
        dense = con.execute("""
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename,
                   1 - (c.embedding <=> %s::vector) AS dense_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE
            ORDER BY c.embedding <=> %s::vector
            LIMIT %s
        """, (vector, corpus_id, vector, top_k)).fetchall()

        lexical = con.execute("""
            SELECT c.id, c.content, c.page_number,
                   c.start_offset, c.end_offset,
                   s.source_key, s.version, s.filename,
                   similarity(c.content, %s) AS lexical_score
            FROM integrity.chunks c
            JOIN integrity.sources s ON s.id = c.source_id
            WHERE s.corpus_id = %s AND s.is_active = TRUE
            ORDER BY similarity(c.content, %s) DESC
            LIMIT %s
        """, (query_text, corpus_id, query_text, top_k)).fetchall()

    merged: dict[int, dict] = {}
    for branch, rows in (("dense", dense), ("lexical", lexical)):
        for rank, row in enumerate(rows, start=1):
            item = merged.setdefault(row["id"], {
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
            })
            item["rrf_score"] += 1.0 / (60 + rank)
            score_key = branch + "_score"
            item[score_key] = float(row[score_key])
    return sorted(merged.values(),
                  key=lambda x: x["rrf_score"], reverse=True)[:fused_k]
```

**Hiệu năng MVP:** đoạn trên thực hiện **hai câu truy vấn cho mỗi query chunk** (dense + trigram), dễ hiểu nhưng có thể tốn thời gian nếu quét hàng trăm chunk. Với corpus 20–50 khóa luận, hãy đo trước. Nếu chậm, tối ưu bằng batch retrieval, giới hạn những chương cần kiểm tra, cache candidates và tạo index phù hợp. Không nên tuyên bố API xử lý “trong vài giây” khi chưa benchmark.

## 10. Code Python — Celery tasks indexing/scanning

### `app/celery_app.py`

```python
from celery import Celery
from app.config import settings

celery_app = Celery(
    "ugtm_plagiarism_ai",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks"],
)
```

### `app/tasks.py`

```python
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
        embeddings = encode_texts([x.text for x in chunks])
        with connect() as con:
            row = con.execute("""
                INSERT INTO integrity.sources
                  (corpus_id, source_key, version, filename, file_sha256)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (corpus_id, source_key, version)
                DO UPDATE SET filename = EXCLUDED.filename,
                              file_sha256 = EXCLUDED.file_sha256
                RETURNING id
            """, (job["corpus_id"], job["document_key"], job["version"],
                  job["filename"], job["file_sha256"])).fetchone()
            source_id = row["id"]
            con.execute("DELETE FROM integrity.chunks WHERE source_id = %s",
                        (source_id,))
            for chunk, vector in zip(chunks, embeddings):
                con.execute("""
                    INSERT INTO integrity.chunks
                    (source_id, chunk_no, page_number, start_offset,
                     end_offset, content, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s::vector)
                """, (source_id, chunk.chunk_no, chunk.page_number,
                      chunk.start_offset, chunk.end_offset,
                      chunk.text, vector_literal(vector)))
            # Chỉ chuyển phiên bản active khi indexing hoàn tất.
            con.execute("""
                UPDATE integrity.sources SET is_active = FALSE
                WHERE corpus_id = %s AND source_key = %s AND id <> %s
            """, (job["corpus_id"], job["document_key"], source_id))
            con.execute("""
                UPDATE integrity.sources
                SET is_active = TRUE, indexed_at = now()
                WHERE id = %s
            """, (source_id,))
            con.execute("""
                UPDATE integrity.jobs
                SET status = 'completed', updated_at = now(),
                    report = %s
                WHERE id = %s::uuid
            """, (Jsonb({"indexed_chunks": len(chunks),
                           "source_id": job["document_key"]}), job_id))
    except Exception:
        logger.exception("Index job failed: %s", job_id)
        set_status(job_id, "failed", "INDEX_ERROR")


@celery_app.task(name="app.tasks.scan_document")
def scan_document(job_id: str):
    set_status(job_id, "processing")
    try:
        job = get_job(job_id)
        with connect() as con:
            count = con.execute("""
                SELECT count(*) AS n FROM integrity.sources
                WHERE corpus_id = %s AND is_active = TRUE
            """, (job["corpus_id"],)).fetchone()["n"]
        if count == 0:
            raise ValueError("CORPUS_EMPTY")

        query_chunks = parse_job(job)
        vectors = encode_texts([x.text for x in query_chunks])
        all_findings: list[dict] = []
        for query_chunk, vector in zip(query_chunks, vectors):
            candidates = retrieve(query_chunk.text, vector, job["corpus_id"])
            if not candidates:
                continue
            pairs = [[query_chunk.text, item["source_text"]]
                     for item in candidates]
            scores = rerank_pairs(pairs)
            for item, score in zip(candidates, scores):
                # Heuristic demo: không phải ngưỡng kết luận đạo văn.
                lexical = item["lexical_score"] or 0.0
                dense = item["dense_score"] or 0.0
                review = lexical >= 0.65 or (dense >= 0.68 and score >= 0.67)
                if not review:
                    continue
                all_findings.append({
                    "submission_page": query_chunk.page_number,
                    "submission_start": query_chunk.start_offset,
                    "submission_end": query_chunk.end_offset,
                    "submission_text": query_chunk.text,
                    **item,
                    "reranker_score": round(score, 4),
                    "review_required": True,
                })

        # Sắp xếp, giữ một vài bằng chứng có điểm tốt nhất trên mỗi chunk.
        all_findings.sort(key=lambda f: f["reranker_score"], reverse=True)
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
                "matched_sources": len({f["source_key"] for f in all_findings}),
            },
            "findings": findings,
            "disclaimer": (
                "Similarity evidence only; teachers must verify sources, "
                "citations and context. No automatic plagiarism verdict."
            ),
        }
        with connect() as con:
            con.execute("""
                UPDATE integrity.jobs
                SET status='completed', report=%s, updated_at=now()
                WHERE id=%s::uuid
            """, (Jsonb(report), job_id))
    except Exception:
        logger.exception("Scan job failed: %s", job_id)
        set_status(job_id, "failed", "SCAN_ERROR")
```

**Ghi chú kỹ thuật:** `flagged_pairs` là số cặp đoạn ứng viên được gắn cờ, **không phải số đoạn duy nhất**; do chunk chồng lấn, có thể nhiều cặp trỏ tới cùng một đoạn hoặc cùng nguồn. Bản UI có thể nhóm theo trang/nguồn để đỡ rối. Lỗi hiện được trả bằng mã chung; worker ghi log riêng. Nếu muốn hiển thị `EMPTY_TEXT`/`CORPUS_EMPTY` cho Admin, phân loại exception cụ thể trước khi lưu `error_code`. MVP trên chỉ đảm bảo state từ `queued` → `processing` → `completed/failed`, chưa có retry scheduler và recovery của job khi worker chết.

## 11. Code Python — HTTP API, upload và kiểm soát truy cập

### `app/main.py`

```python
import hashlib
import re
import secrets
import uuid
import zipfile
from pathlib import Path

from fastapi import (
    Depends, FastAPI, File, Form, Header,
    HTTPException, UploadFile,
)
from app.config import settings
from app.db import connect, get_job
from app.tasks import index_source, scan_document

app = FastAPI(title="UGTM Plagiarism AI", version="0.1.0")
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")):
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


def validate_metadata(corpus_id: str, document_key: str, version: int):
    if (not SAFE_KEY.fullmatch(corpus_id)
        or not SAFE_KEY.fullmatch(document_key)
        or version < 1):
        raise HTTPException(status_code=422, detail="Invalid metadata")


async def save_upload(file: UploadFile) -> tuple[str, str, str]:
    filename = Path(file.filename or "unnamed").name[:180]
    ext = Path(filename).suffix.lower()
    if ext not in {".pdf", ".docx"}:
        raise HTTPException(status_code=415, detail="Only PDF/DOCX allowed")
    upload_dir = settings.data_dir / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{uuid.uuid4().hex}{ext}"
    digest = hashlib.sha256()
    count = 0
    try:
        with path.open("wb") as dest:
            while data := await file.read(1024 * 1024):
                count += len(data)
                if count > settings.max_upload_mb * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="File too large")
                digest.update(data)
                dest.write(data)
        if count == 0:
            raise HTTPException(status_code=400, detail="Empty file")
        if ext == ".pdf":
            with path.open("rb") as src:
                if not src.read(5).startswith(b"%PDF-"):
                    raise HTTPException(status_code=415, detail="Invalid PDF")
        else:
            if not zipfile.is_zipfile(path):
                raise HTTPException(status_code=415, detail="Invalid DOCX")
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise HTTPException(status_code=415, detail="Invalid DOCX")
                # Giới hạn tổng dung lượng giải nén, chống ZIP bomb cơ bản.
                if sum(f.file_size for f in archive.infolist()) > 100 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="Expanded DOCX too large")
        return str(path), digest.hexdigest(), filename
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


def insert_job(job_type: str, corpus_id: str, document_key: str,
               version: int, filename: str, path: str, sha: str):
    job_id = str(uuid.uuid4())
    with connect() as con:
        created = con.execute("""
            INSERT INTO integrity.jobs
              (id, job_type, corpus_id, document_key, version,
               filename, file_path, file_sha256)
            VALUES (%s::uuid,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (job_type, corpus_id, document_key, version)
            DO NOTHING RETURNING id
        """, (job_id, job_type, corpus_id, document_key,
              version, filename, path, sha)).fetchone()
        if created:
            return job_id, True
        existing = con.execute("""
            SELECT id, file_sha256 FROM integrity.jobs
            WHERE job_type=%s AND corpus_id=%s AND document_key=%s
              AND version=%s
        """, (job_type, corpus_id, document_key, version)).fetchone()
        if existing["file_sha256"] != sha:
            raise HTTPException(status_code=409,
                                detail="Version already exists with different content")
        return str(existing["id"]), False


@app.get("/healthz")
def healthz():
    # Readiness thực tế cần kiểm tra thêm Redis, worker, database.
    return {"status": "ok"}


@app.post("/api/v1/corpus/documents", status_code=202,
          dependencies=[Depends(require_key)])
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
            "index", corpus_id, source_id, version, filename, path, sha
        )
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
    if created:
        index_source.delay(job_id)
    else:
        Path(path).unlink(missing_ok=True)
    return {"job_id": job_id, "status": "queued" if created else "existing"}


@app.post("/api/v1/scans", status_code=202,
          dependencies=[Depends(require_key)])
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
            "scan", corpus_id, submission_id, version, filename, path, sha
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
```

### API contract

| HTTP | Endpoint | Form/body | Kết quả |
|---|---|---|---|
| `POST` | `/api/v1/corpus/documents` | `corpus_id`, `source_id`, `version`, `file` | `202` + `job_id` |
| `POST` | `/api/v1/scans` | `corpus_id`, `submission_id`, `version`, `file` | `202` + `job_id` |
| `GET` | `/api/v1/jobs/{job_id}` | Path UUID | `queued/processing/completed/failed` |
| `GET` | `/api/v1/reports/{job_id}` | Path UUID | Báo cáo JSON hoặc `409` |
| `GET` | `/healthz` | — | Liveness của API |

Tất cả endpoint trừ `/healthz` cần header **`X-API-Key`**. `source_id` và `submission_id` là ID nghiệp vụ **do .NET quản lý**, không phải tên file. `version` là phiên bản nộp. Một bộ `(job_type, corpus_id, document_id, version)` được coi là idempotent: nếu upload lại đúng nội dung sẽ trả `job_id` cũ; nếu cùng ID/version nhưng nội dung khác sẽ bị `409`. Muốn quét lại sau khi thay đổi model cần thiết kế `run_version`/`model_version` hoặc endpoint reprocess riêng — không âm thầm ghi đè báo cáo cũ.

**Cảnh báo:** Nếu Celery/Redis bị lỗi ngay lúc `.delay()`, job có thể kẹt `queued` dù API đã lưu thành công. Bản production phải dùng transactional outbox hoặc bộ quét job `queued` quá hạn để đẩy lại vào queue. MVP cần có cách Admin thủ công retry/restart; không xóa bài sinh viên khi AI lỗi.

## 12. Response JSON mẫu cho giao diện .NET/React

```json
{
  "schema_version": 1,
  "job_id": "d13bff7f-9d84-40cf-88a9-ae70fa92d41c",
  "corpus_id": "uit-library-v1",
  "submission_id": "thesis-2026-001",
  "submission_version": 1,
  "model": {
    "embedding": "BAAI/bge-m3",
    "reranker": "BAAI/bge-reranker-v2-m3"
  },
  "summary": {
    "scanned_chunks": 35,
    "flagged_pairs": 6,
    "findings_returned": 6,
    "matched_sources": 2
  },
  "findings": [
    {
      "submission_page": 12,
      "submission_start": 100,
      "submission_end": 430,
      "submission_text": "Đoạn trong báo cáo mới...",
      "chunk_id": 91,
      "source_key": "kltn-2024-025",
      "source_version": 1,
      "source_filename": "kltn-2024-025.pdf",
      "source_page": 18,
      "source_start": 240,
      "source_end": 580,
      "source_text": "Đoạn nguồn được tìm thấy...",
      "rrf_score": 0.0323,
      "dense_score": 0.84,
      "lexical_score": 0.31,
      "reranker_score": 0.91,
      "review_required": true
    }
  ],
  "disclaimer": "Similarity evidence only; teachers must verify sources, citations and context. No automatic plagiarism verdict."
}
```

**Lưu ý:** Đây là **JSON minh họa**, không phải kết quả benchmark. Các trường `dense_score` hoặc `lexical_score` có thể là `null` nếu ứng viên chỉ được trả về từ một nhánh tìm kiếm. Reranker score không biểu diễn xác suất vi phạm. **Không** dùng `matched_sources` như số nguồn đạo văn đã chứng minh.

## 13. Tích hợp với ASP.NET Core UGTM

### Ranh giới trách nhiệm

- Chỉ **UGTM** biết người nộp, đề tài, GVHD, GVPB, trạng thái nghiệp vụ, quyền xem report.
- AI **không** nhận JWT sinh viên, MSSV, điểm chấm hoặc thông tin cá nhân không cần thiết; chỉ nhận `submission_id`/`corpus_id`/`version` và nội dung file.
- UGTM lưu: `AIJobId`, `SubmissionId`, `Version`, `Status`, `RequestedAt`, `CompletedAt`, `ModelVersion` tùy quy ước hiện có. AI lưu corpus, chunks, evidence/report. Tránh thiết lập foreign key xuyên hai database.
- Khi phiên bản báo cáo thay đổi, tạo scan mới theo `version`. Kết quả của phiên bản cũ chỉ là lịch sử, không bị coi là nguồn độc lập của sinh viên khác.
- Sinh viên upload thành công **kể cả khi AI đang chạy hoặc AI bị lỗi**. GVHD/GVPB xem báo cáo khi đã hoàn tất, tuân thủ phạm vi được phân công (RBAC + policy nghiệp vụ).

### Ví dụ adapter `PlagiarismAiClient.cs` (tham khảo, chỉnh namespace theo repo thật)

```csharp
using System.Net.Http.Headers;
using System.Text.Json;

public sealed class PlagiarismAiClient(HttpClient client)
{
    public async Task<string> StartScanAsync(
        string submissionId,
        int version,
        string corpusId,
        Stream documentStream,
        string originalFilename,
        CancellationToken ct = default)
    {
        using var form = new MultipartFormDataContent();
        form.Add(new StringContent(corpusId), "corpus_id");
        form.Add(new StringContent(submissionId), "submission_id");
        form.Add(new StringContent(version.ToString()), "version");

        using var file = new StreamContent(documentStream);
        file.Headers.ContentType = new MediaTypeHeaderValue(
            originalFilename.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase)
              ? "application/pdf"
              : "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        );
        form.Add(file, "file", Path.GetFileName(originalFilename));

        using var response = await client.PostAsync(
            "/api/v1/scans", form, ct);
        response.EnsureSuccessStatusCode();
        using var result = JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct));
        return result.RootElement.GetProperty("job_id").GetString()!;
    }

    public async Task<JsonDocument> GetJobAsync(
        string jobId, CancellationToken ct = default)
    {
        using var response = await client.GetAsync(
            $"/api/v1/jobs/{jobId}", ct);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct));
    }

    public async Task<JsonDocument> GetReportAsync(
        string jobId, CancellationToken ct = default)
    {
        using var response = await client.GetAsync(
            $"/api/v1/reports/{jobId}", ct);
        response.EnsureSuccessStatusCode();
        return JsonDocument.Parse(
            await response.Content.ReadAsStringAsync(ct));
    }
}
```

**Dependency injection** trong host UGTM (chỉnh theo `Program.cs`/composition root thực tế):

```csharp
builder.Services.AddHttpClient<PlagiarismAiClient>((sp, client) =>
{
    var config = sp.GetRequiredService<IConfiguration>();
    client.BaseAddress = new Uri(config["PlagiarismAI:BaseUrl"]!);
    client.DefaultRequestHeaders.Add(
        "X-API-Key", config["PlagiarismAI:ApiKey"]!);
    client.Timeout = TimeSpan.FromMinutes(2); // upload HTTP, không chờ AI infer
});
```

```json
{
  "PlagiarismAI": {
    "BaseUrl": "http://localhost:8000",
    "ApiKey": "USE_ENV_OR_SECRET_MANAGER_IN_REAL_DEPLOYMENT"
  }
}
```

Không commit `ApiKey` thực vào `appsettings.json`; override qua biến môi trường hoặc secret manager. Nếu .NET chạy **trong cùng Docker network** thì đổi URL thành `http://api:8000`. Nếu khác host, dùng HTTPS reverse proxy/mTLS và ràng buộc firewall/IP allowlist.

### Luồng nghiệp vụ trong .NET (pseudo-implementation)

```text
1. Sinh viên upload report.pdf lên UGTM.
2. .NET xác thực RBAC/Policy; lưu file vào UGTM storage;
   ghi nhận SubmissionVersion + DB transaction COMMIT.
3. Phát sự kiện nội bộ ReportSubmitted(submissionId, version, fileKey).
4. Background handler của UGTM đọc file từ storage -> gọi
   PlagiarismAiClient.StartScanAsync(...) -> lưu AIJobId.
5. Background scheduler của UGTM GET /jobs/{id}; cập nhật status.
6. Khi completed, tải GET /reports/{id}; lưu snapshot hoặc ID tham chiếu.
7. GVHD/GVPB được phân công GET /ugtm/reports/{submissionId}/integrity.
   Backend UGTM thực hiện authorize, rồi trả dữ liệu cho React.
8. Nếu AI Failed: cho phép retry sau; không rollback file nộp KLTN.
```

**Vị trí tích hợp được khuyên dùng:** bước nộp **báo cáo cuối kỳ** (tuần 11–18), trước quá trình nhận xét/chuyển phản biện. Có thể mở rộng sang đề cương và báo cáo giữa kỳ khi MVP ổn định. Trong báo cáo phân tích của nhóm, thẩm định KLTN và quyền truy cập gắn với GVHD/GVPB/Khoa theo nhiệm vụ; tính năng mới phải tuân thủ đúng phạm vi đó.

### Mẫu UI tối thiểu (React)

```text
[Chi tiết khóa luận]
  File: BaoCaoCuoiKy_v3.pdf
  AI Integrity: Đang kiểm tra ...

  Sau khi hoàn tất:
  • Đoạn cần rà soát: 6 cặp
  • Tài liệu nguồn liên quan: 2

  [Đoạn báo cáo: Trang 12]    [Nguồn: KLTN 2024/025, Trang 18]
  "..."                      "..."
  Lexical: 0.31  |  Semantic: 0.84  |  Reranker: 0.91
  [Cần GV kiểm tra trích dẫn và bối cảnh]
```

Hiển thị **số cặp cần rà soát** và bằng chứng thay vì một “tỷ lệ đạo văn” duy nhất. Không hiện các tài liệu nguồn chưa được phép cho sinh viên đọc; có thể chỉ để giảng viên xem toàn văn đối chiếu.

## 14. Chạy MVP từ đầu đến cuối

### Bước 1 — Dựng môi trường

```bash
cp .env.example .env
# Sửa POSTGRES_PASSWORD và AI_API_KEY trong .env
docker compose build
docker compose up -d

docker compose ps
docker compose logs -f worker
```

Lần đầu Celery worker tải model từ Hugging Face, cần Internet và dung lượng ổ đĩa cho cache. Tại thời điểm khởi động, không bắt buộc API phải đợi worker tải model để trả `/healthz`.

### Bước 2 — Test GPU trong container

```bash
docker compose exec worker python -c \
 'import torch; print("CUDA:", torch.cuda.is_available()); print(torch.cuda.get_device_name(0))'
```

### Bước 3 — Index một file nguồn được phép sử dụng

```bash
export AI_API_KEY='the-same-value-in-.env'

curl -X POST http://127.0.0.1:8000/api/v1/corpus/documents \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "source_id=kltn-demo-001" \
  -F "version=1" \
  -F "file=@./samples/source.pdf"
```

Response ví dụ:

```json
{"job_id": "84d7ef55-4b99-4a63-88cf-28e4a493deae", "status": "queued"}
```

**Quan trọng:** Kiểm tra job index đã `completed` trước khi gọi scan, nếu không corpus sẽ bị coi là chưa sẵn sàng.

### Bước 4 — Kiểm tra job

```bash
curl -H "X-API-Key: $AI_API_KEY" \
  http://127.0.0.1:8000/api/v1/jobs/84d7ef55-4b99-4a63-88cf-28e4a493deae
```

### Bước 5 — Scan báo cáo thử

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scans \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "submission_id=thesis-demo-2026-001" \
  -F "version=1" \
  -F "file=@./samples/submission.pdf"
```

Dùng `job_id` trả về để `GET /api/v1/jobs/{job_id}` rồi `GET /api/v1/reports/{job_id}` khi hoàn tất.

**Test âm tính:** tạo tài liệu mới có cùng chủ đề nhưng diễn giải phương pháp/kết quả khác với tài liệu nguồn; kiểm tra số cảnh báo giả. **Test dương tính:** dùng một phần nội dung từ tài liệu nguồn mà nhóm có quyền kiểm thử rồi thử sửa câu chữ; bảo đảm corpus đã index trước.

## 15. Kiểm thử tự động căn bản

### `tests/test_documents.py`

```python
from app.documents import TextPage, make_chunks, normalize


def test_normalize():
    assert normalize("Xin  chào\nUIT\t!") == "Xin chào UIT !"


def test_chunk_offsets_are_valid():
    text = " ".join(f"word{i}" for i in range(250))
    result = make_chunks([TextPage(3, text)], size=90, overlap=20)
    assert len(result) >= 3
    assert all(c.page_number == 3 for c in result)
    assert all(text[c.start_offset:c.end_offset] == c.text for c in result)
    assert result[1].start_offset < result[0].end_offset
```

```bash
docker compose exec api pytest -q tests
```

**Chú ý:** `Dockerfile` MVP chỉ copy thư mục `app`, không copy `tests`. Để chạy lệnh pytest trên, sửa Dockerfile thành `COPY tests ./tests` **trong môi trường dev** hoặc mount `tests` vào container. Không nhất thiết đóng gói test vào image production.

### Checklist kiểm thử tích hợp (phải làm khi kết nối UGTM)

| Tình huống | Mong đợi |
|---|---|
| PDF/DOCX hợp lệ, dưới 20 MB | Tạo job `queued` |
| File `.exe` đổi tên `.pdf`, hoặc PDF header sai | Từ chối `415` |
| File vượt 20 MB | Từ chối `413`, xóa file tạm |
| Request không có `X-API-Key` | `401` |
| Trùng `source_id + version`, cùng bytes | Idempotent, không index lại |
| Trùng ID/version, bytes khác | `409` |
| Corpus chưa index | Job scan `failed` (`SCAN_ERROR` trong MVP) |
| PDF scan không có text | Job `failed` (`INDEX_ERROR` hoặc `SCAN_ERROR`) |
| 2 PDF giống nhau theo cùng nguồn | Có candidates, GV xem được source/page |
| Hai tài liệu cùng lĩnh vực, không sao chép | Theo dõi false positives |
| API worker chết trong lúc chạy | Được phát hiện và có quy trình vận hành/retry thủ công |
| Sinh viên nộp bài khi AI lỗi | UGTM vẫn lưu bài thành công |
| Người ngoài nhóm/GV không được phân công | UGTM chặn xem report theo policy |
| Nộp version mới | Tạo job mới, không ghi đè report version cũ |

## 16. Dữ liệu 20–50 khóa luận từ thư viện UIT

### Cần xin thư viện những gì?

1. **Quyền sử dụng cụ thể:** được phép xử lý tự động, lưu text chunks/embeddings, giữ file bao lâu, ai được xem toàn văn/đoạn trích và có được triển khai trên GPU thuê ngoài không.
2. **Format:** ưu tiên PDF có text layer hoặc DOCX. Nếu được cấp PDF scan, lên kế hoạch OCR sau; không đưa vào MVP mà không kiểm tra.
3. **Metadata:** mã nội bộ đã khử định danh, tên tài liệu, năm, lĩnh vực, tình trạng công bố, phiên bản, quyền truy cập. Không đưa MSSV hoặc danh sách điểm vào AI khi không cần.
4. **Phạm vi:** 20–50 tài liệu đủ để xây **demo đối chiếu nội bộ**, nhưng không được tuyên bố bao phủ mọi tài liệu UIT hoặc Internet.
5. **Quy trình thu hồi:** nếu thư viện rút quyền sử dụng một tài liệu, cần endpoint xóa/deactivate nguồn và chunks/vector, đồng thời xử lý snapshot báo cáo cũ theo chính sách.

### Cách chuẩn bị trước khi có dữ liệu

- Dùng 5–10 tài liệu do nhóm tự tạo hoặc có giấy phép cho phép lập chỉ mục.
- Tạo 3 nhóm test: `EXACT_COPY`, `PARAPHRASE`, `UNRELATED_SAME_TOPIC` và một số cặp Việt–Anh.
- Giữ bảng test đơn giản (`test_id, source_id, submission_id, expected_source, note`) để demo với thầy.
- Chỉ số cần quan sát: nguồn nào được tìm thấy trong top-K, vị trí đoạn, cảnh báo giả, thời gian index một PDF và scan một PDF.
- Khi dữ liệu thư viện đến, chạy `POST /corpus/documents` cho từng tài liệu, chờ job `completed`. Không cần train lại BGE-M3.

**Đạo văn ≠ tương đồng:** việc trích dẫn đúng, quy chuẩn biểu mẫu, cùng kiến thức nền và hai đề tài có chủ đề gần nhau có thể làm điểm cao. Báo cáo phải ghi rõ chỉ hỗ trợ kiểm tra bằng chứng.

## 17. Bảo mật, chất lượng và vận hành trước khi đưa lên máy chủ thật

### Tối thiểu trước demo

- GitHub repo AI **private**; `.gitignore` đầy đủ cho `.env`, `data/`, `models/`, `*.pdf`, `*.docx`, database dumps, logs nhạy cảm.
- API không public; backend UGTM là cửa kiểm soát quyền duy nhất đối với sinh viên và giảng viên. Secret qua env, không để browser chứa `X-API-Key`.
- File upload giới hạn size, extension và signature, validate DOCX zip; chạy container không root và quét malware nếu hạ tầng có điều kiện.
- Log lỗi AI nhưng tránh log toàn văn tài liệu hay nội dung file; không gửi text khóa luận sang dịch vụ LLM bên ngoài khi chưa có phép.
- Mỗi scan phải gắn với **phiên bản báo cáo** và **corpus/model version** để có thể giải thích kết quả.
- Đo `time_to_index`, `time_to_scan`, VRAM tối đa, số chunks, số cặp rerank, worker queue depth; thử nghiệm với PDF dài thực tế.

### Nâng cấp sau MVP, ưu tiên từ cao đến thấp

1. **Khả dụng:** transactional outbox + retry/backoff + timeout + dead-letter/queued recovery; trạng thái `cancelled` nếu sinh viên thay thế file khi job chưa chạy.
2. **Chất lượng đầu vào:** parsing theo heading/paragraph, loại mục tài liệu tham khảo/biểu mẫu đặc thù, OCR PDF scan có kiểm soát chất lượng.
3. **Kết quả:** nhóm các hit chồng lấn, hỗ trợ teacher feedback `confirmed/rejected/needs_review`, tổng hợp phạm vi text được bao phủ, chưa gọi đó là % đạo văn.
4. **Citation-aware:** parser trích dẫn/tài liệu tham khảo và xác định trích dẫn liên quan đến vị trí matched passage. Không giả định “có cite => không đạo văn”.
5. **Hiệu năng:** batch vector retrieval, HNSW nếu lớn, model batch và async worker autoscaling theo VRAM.
6. **Lưu trữ:** object storage riêng; retention/delete theo chính sách; encrypt at rest; audit log cho thao tác xem/xóa nguồn.
7. **Quan sát:** OpenTelemetry metrics/log/trace; kiểm thử hợp đồng API từ .NET; CI/CD build image cố định phiên bản.
8. **Bảo mật người dùng:** policy theo submission/assignment; kiểm tra vai trò giảng viên ở .NET; tách quyền Admin của thư viện; kiểm tra giới hạn corpus để tránh lộ tài liệu giữa khoa/đợt.

## 18. Lộ trình triển khai đề xuất cho nhóm

| Sprint | Công việc | Tiêu chí hoàn thành |
|---|---|---|
| 1 | Khởi tạo repo, Docker, CUDA, Postgres, FastAPI, Celery | `/healthz`, worker thấy GPU, schema tạo được |
| 2 | Parser + chunking + BGE-M3 indexing | Index được 5 PDF/DOCX mẫu, có chunks/vectors |
| 3 | Dense search + pg_trgm + RRF + reranker | Test copy/paraphrase tìm được nguồn và trả JSON evidence |
| 4 | .NET integration + React view + quyền | Nộp bài UGTM → AI queued → completed → giảng viên xem |
| 5 (nếu có) | Dữ liệu thư viện, chuẩn hóa, test hiệu năng, xử lý false positives | Nhập 20–50 KLTN được phép và demo end-to-end |

**Tiêu chí báo cáo với thầy:** Không trình bày đây là “AI tự kết luận đạo văn”. Hãy demo ba case: **copy nguyên văn**, **diễn đạt lại**, **cùng chủ đề nhưng độc lập**; trình bày rõ vì sao lexical và semantic bổ sung nhau, chứng minh trace được nguồn và giảng viên có quyền đánh giá.

## 19. Rủi ro và quyết định cần chốt khi bắt tay vào code

| Vấn đề | Phương án mặc định | Khi nào cần đổi |
|---|---|---|
| Repo UGTM chưa review được | Module tích hợp .NET chỉ là adapter mẫu | Khi cấp quyền repo, map vào Events/Contracts thật |
| Dữ liệu thư viện chưa có | Corpus tài liệu được phép + bộ test mô phỏng | Khi thư viện gửi tài liệu và cấp quyền |
| GPU loại/VRAM chưa rõ | Một worker, fp16, batch=8 | CUDA OOM → batch=2/4 hoặc tách model |
| Quét PDF dài | Background job | Tối ưu sau khi benchmark thật |
| PDF scan | Báo lỗi `EMPTY_TEXT` | Khi bổ sung OCR |
| Có nhiều phiên bản cùng nguồn | Chỉ latest indexed là active | Cần quản trị version cụ thể/audit |
| Có AI score cao | Chỉ `review_required`, GV thẩm định | Khi có quy chế/nhãn dữ liệu để hiệu chỉnh |
| Quyền xem khóa luận nguồn | Chỉ giảng viên được cấp quyền ở UGTM | Theo thỏa thuận thư viện |
| Schema/vector model đổi | Re-index corpus theo model/version | Khi cập nhật embedding model |

## 20. Nguồn kỹ thuật tham khảo

Các nguồn dưới đây là **tham khảo kỹ thuật bên ngoài**, không phải tính năng đã được xác thực trong repo UGTM:

- BAAI BGE-M3 model card và ví dụ `BGEM3FlagModel`: <https://huggingface.co/BAAI/bge-m3>
- BGE Reranker v2 M3 và ví dụ `FlagReranker`: <https://huggingface.co/BAAI/bge-reranker-v2-m3>
- FlagEmbedding inference examples: <https://github.com/FlagOpen/FlagEmbedding/tree/master/examples/inference>
- pgvector (cosine `<=>`, exact search, HNSW, hybrid): <https://github.com/pgvector/pgvector>
- PostgreSQL pg_trgm: <https://www.postgresql.org/docs/current/pgtrgm.html>
- FastAPI upload: <https://fastapi.tiangolo.com/tutorial/request-files/>
- Celery user guide: <https://docs.celeryq.dev/en/stable/userguide/index.html>
- Docker Compose GPU: <https://docs.docker.com/compose/how-tos/gpu-support/>
- UGTM (đường dẫn do nhóm cung cấp, chưa mở được qua kết nối hiện tại): <https://github.com/karasuma-2401/UGTM>

---

### Kết luận

**Bắt đầu bằng repo `UGTM-Plagiarism-AI` chạy riêng trên GPU, 4 endpoint nội bộ, 2 background tasks, PostgreSQL+pgvector và pipeline Hybrid Search + Reranking.** Sau khi demo độc lập ổn định, kết nối vào **luồng nộp báo cáo cuối kỳ** của UGTM qua background event handler. Tiếp nhận corpus 20–50 khóa luận từ thư viện bằng pipeline indexing riêng, giữ nguyên cơ chế bảo mật/quyền xem của hệ thống trường. Ưu tiên chức năng **tìm nguồn và giải thích bằng chứng**, không tạo “tỷ lệ đạo văn” thiếu cơ sở.
