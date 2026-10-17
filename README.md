# UGTM-Plagiarism-AI

Dịch vụ nội bộ hỗ trợ kiểm tra **mức độ tương đồng học thuật** cho UGTM. Hệ thống tìm và trả về bằng chứng đối chiếu để giảng viên rà soát; không tự động kết luận đạo văn và không tự động chặn bài nộp.

## Kiến trúc MVP

- FastAPI tiếp nhận PDF/DOCX, tạo job và cung cấp trạng thái/báo cáo.
- Celery + Redis chạy indexing và scanning ngoài HTTP request.
- Worker NVIDIA GPU dùng `BAAI/bge-m3` và `BAAI/bge-reranker-v2-m3`.
- PostgreSQL 17 + `pgvector` + `pg_trgm` lưu corpus và chạy hybrid retrieval.
- RRF hợp nhất lexical/dense ranking trước khi rerank.

## Yêu cầu

- Linux hoặc WSL2 có NVIDIA driver, Docker Engine, Docker Compose plugin và NVIDIA Container Toolkit.
- `nvidia-smi` hoạt động trên host.
- Corpus chỉ chứa tài liệu mà nhóm được phép xử lý và lập chỉ mục.

## Khởi chạy

1. Tạo file môi trường từ `.env.example` và thay hai secret mẫu:

   ```bash
   cp .env.example .env
   ```

2. Build và chạy các service:

   ```bash
   docker compose build
   docker compose up -d
   docker compose ps
   docker compose logs -f worker
   ```

3. Xác nhận worker thấy GPU:

   ```bash
   docker compose exec worker python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
   ```

4. Kiểm tra API:

   ```bash
   curl http://127.0.0.1:8000/healthz
   ```

Lần chạy đầu worker cần Internet để tải model và có thể mất vài phút. API vẫn có thể trả `/healthz` trong thời gian đó.

## API nội bộ

Tất cả endpoint trừ `/healthz` cần header `X-API-Key`.

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/v1/corpus/documents` | Index một PDF/DOCX nguồn |
| `POST` | `/api/v1/scans` | Tạo job scan bài nộp |
| `GET` | `/api/v1/jobs/{job_id}` | Xem trạng thái job |
| `GET` | `/api/v1/reports/{job_id}` | Lấy báo cáo khi job hoàn tất |

Ví dụ index tài liệu nguồn:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/corpus/documents \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "source_id=kltn-demo-001" \
  -F "version=1" \
  -F "file=@./samples/source.pdf"
```

Chờ job index ở trạng thái `completed`, sau đó scan bài thử:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scans \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "submission_id=thesis-demo-001" \
  -F "version=1" \
  -F "file=@./samples/submission.pdf"
```

## Kiểm thử

```bash
docker compose run --rm api pytest -q tests
```

Các test hiện tại kiểm tra normalize và tính đúng của chunk offsets. Kiểm thử end-to-end cần PostgreSQL, Redis, GPU và bộ PDF/DOCX mà nhóm có quyền sử dụng.

## Giới hạn MVP

- Không OCR PDF scan, không xử lý đáng tin cậy PDF nhiều cột/bảng/hình phức tạp.
- Offset là vị trí trong text đã normalize, không phải tọa độ highlight trên PDF.
- DOCX chưa có page number ổn định và parser hiện chỉ đọc paragraph.
- Các ngưỡng `review_required` chỉ là heuristic khởi tạo để demo; phải hiệu chỉnh bằng dữ liệu thật.
- Chưa có transactional outbox, retry/dead-letter, queued-job recovery, retention và observability production.
- Không tính hoặc công bố “phần trăm đạo văn”.

Tài liệu thiết kế đầy đủ nằm tại [UGTM-Plagiarism-AI.md](./UGTM-Plagiarism-AI.md).
