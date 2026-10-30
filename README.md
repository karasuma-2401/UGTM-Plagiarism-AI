# UGTM-Plagiarism-AI

Dịch vụ nội bộ hỗ trợ kiểm tra **mức độ tương đồng học thuật** cho hệ thống UGTM. Hệ thống tìm và trả về bằng chứng đối chiếu để giảng viên rà soát; không tự động kết luận đạo văn và không tự động chặn bài nộp.

## Kiến trúc v2 (Hybrid Pipeline + Custom Classifier)

- **FastAPI**: Tiếp nhận PDF/DOCX, tạo background job và cung cấp trạng thái/báo cáo (`schema_version: 2`).
- **Celery + Redis**: Chạy indexing và scanning ngoài HTTP request để không chặn thao tác nộp bài của sinh viên.
- **Worker AI**:
  - Trích xuất văn bản từ PDF/DOCX và chia đoạn cửa sổ (90 từ, 20 từ overlap).
  - Dense similarity: `BAAI/bge-m3` (1024-dim vector).
  - Lexical similarity: PostgreSQL `pg_trgm.similarity` trên đoạn văn.
  - Fusion: Reciprocal Rank Fusion (RRF).
  - Reranker: `BAAI/bge-reranker-v2-m3` chấm điểm từng cặp ứng viên.
  - Feature Engineering: 5 tín hiệu ổn định (`semantic_cosine`, `reranker_score`, `char_trigram_dice`, `token_jaccard`, `length_ratio`).
  - Decision Logic: Heuristic rule (khi tắt classifier) hoặc Custom Classifier (`scikit-learn` Logistic Regression do nhóm tự huấn luyện) kết hợp near-exact override.
  - Passage Alignment: Gộp các phát hiện liền kề thành nhóm (`passage_group_id`) giúp giảng viên dễ theo dõi.
- **PostgreSQL 17 + pgvector + pg_trgm**: Quản lý `jobs`, `sources` và `chunks`.

## Hai giai đoạn triển khai

- **Giai đoạn A (Mặc định)**: `CLASSIFIER_ENABLED=false`. Pipeline chạy trọn vẹn từ trích xuất, embedding, tìm kiếm hybrid, RRF, reranking và passage alignment; cờ cần rà soát sử dụng rule heuristic khởi tạo.
- **Giai đoạn B (Sau khi có dữ liệu gán nhãn)**: Huấn luyện classifier trên CPU bằng script `training/train_classifier.py`, xuất artifact `.joblib` và `.manifest.json`, sau đó bật `CLASSIFIER_ENABLED=true`.

## Yêu cầu

- Linux hoặc WSL2 có NVIDIA driver, Docker Engine, Docker Compose plugin và NVIDIA Container Toolkit (nếu chạy GPU worker).
- Trong giai đoạn phát triển CPU, có thể chạy và kiểm thử độc lập các module parser, API, feature calculation, alignment và classifier.
- Corpus chỉ chứa tài liệu mà nhóm được phép xử lý và lập chỉ mục (ví dụ: các khóa luận KLTN được thư viện UIT cấp phép).

## Khởi chạy với Docker Compose

1. Tạo file môi trường từ `.env.example` và cấu hình mật khẩu/khóa bí mật:

   ```bash
   cp .env.example .env
   ```

2. Tạo sẵn các thư mục mount (tránh Docker daemon tạo với quyền root):

   ```bash
   mkdir -p artifacts training_data
   ```

3. Build và khởi động các container:

   ```bash
   docker compose build
   docker compose up -d
   docker compose ps
   docker compose logs -f worker
   ```

4. Xác nhận worker nhận diện GPU (khi có GPU):

   ```bash
   docker compose exec worker python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
   ```

5. Kiểm tra API health:

   ```bash
   curl http://127.0.0.1:8000/healthz
   ```

## API nội bộ

Tất cả endpoint nghiệp vụ yêu cầu header `X-API-Key`.

| Method | Endpoint | Mục đích |
|---|---|---|
| `POST` | `/api/v1/corpus/documents` | Index một PDF/DOCX nguồn vào corpus |
| `POST` | `/api/v1/scans` | Tạo job scan bài nộp |
| `GET` | `/api/v1/jobs/{job_id}` | Xem trạng thái job (`queued`/`processing`/`completed`/`failed`) |
| `GET` | `/api/v1/reports/{job_id}` | Lấy báo cáo chi tiết khi job hoàn tất |
| `GET` | `/healthz` | Kiểm tra liveness của service |

### Index tài liệu nguồn

```bash
curl -X POST http://127.0.0.1:8000/api/v1/corpus/documents \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "source_id=kltn-demo-001" \
  -F "version=1" \
  -F "file=@./samples/source.pdf"
```

### Quét bài nộp

```bash
curl -X POST http://127.0.0.1:8000/api/v1/scans \
  -H "X-API-Key: $AI_API_KEY" \
  -F "corpus_id=uit-library-v1" \
  -F "submission_id=thesis-demo-001" \
  -F "version=1" \
  -F "file=@./samples/submission.pdf"
```

## Huấn luyện Custom Classifier (Giai đoạn B)

Chuẩn bị file `training_data/pairs.csv` chứa các cặp đoạn được kiểm tra nhãn (`pair_id,group_id,label,query_text,source_text`), sau đó chạy:

```bash
python -m training.train_classifier \
  --input training_data/pairs.csv \
  --output artifacts/suspicious_passage_classifier.joblib
```

Sau khi hoàn tất và kiểm thử đạt chỉ tiêu, cấu hình trong `.env`:

```dotenv
CLASSIFIER_ENABLED=true
CLASSIFIER_PATH=/artifacts/suspicious_passage_classifier.joblib
```

Và restart lại Celery worker.

### Huấn luyện với Kaggle GPU

Không đưa corpus PDF/DOCX đầy đủ lên Kaggle nếu chưa có văn bản cho phép xử lý trên hạ tầng cloud. Quy trình an toàn là tạo code bundle bằng script, upload bundle và `pairs.csv` đã được phê duyệt vào **hai Kaggle Dataset private** riêng biệt, rồi mở notebook có sẵn tại [kaggle/kaggle_train_classifier.ipynb](./kaggle/kaggle_train_classifier.ipynb).

Hướng dẫn đầy đủ về phân loại dữ liệu, tạo manifest corpus ẩn danh và index qua API nội bộ nằm tại [docs/kaggle-training-and-corpus.md](./docs/kaggle-training-and-corpus.md).

## Kiểm thử

```bash
docker compose run --rm api pytest -q tests
```

Bộ kiểm thử bao gồm:
- `tests/test_documents.py`: Kiểm tra normalize văn bản và chia đoạn chunks/offsets.
- `tests/test_features.py`: Kiểm tra tính nhất quán và giới hạn của 5 đặc trưng.
- `tests/test_alignment.py`: Kiểm tra thuật toán nhóm các đoạn tương đồng liền kề (`passage_group_id`).
- `tests/test_classifier.py`: Kiểm tra cơ chế tải model, kiểm tra schema/manifest mismatch và xử lý khi tắt/bật feature flag.

## Giới hạn hiện tại

- Không OCR PDF scan; yêu cầu PDF có text layer.
- Offset là vị trí ký tự trong văn bản đã normalize, không phải tọa độ highlight PDF.
- DOCX chưa có số trang ổn định và chỉ trích xuất đoạn văn (paragraphs).
- Không tự động kết luận đạo văn và không công bố một chỉ số "phần trăm đạo văn" thiếu căn cứ học thuật.

Tài liệu thiết kế chi tiết và hướng dẫn tích hợp: xem [UGTM-Plagiarism-AI.md](./UGTM-Plagiarism-AI.md).

## Kế hoạch nâng cấp AI

Phạm vi nâng cấp dịch vụ AI cho UGTM, triển khai bởi một người trong khoảng 6 tuần:

- [Kế hoạch nghiên cứu và kiến trúc phát triển Render–Neon–Colab](./docs/research-product-upgrade-plan-2026-10-29.md).
- [Checklist theo checkpoint: task, đầu ra và tiêu chí hoàn tất](./docs/ai-development-checkpoints.md).

Các tài liệu này mô tả công việc dự kiến, không phải xác nhận tính năng đã triển khai. Mục tiêu là nền tảng kỹ thuật có thể phát triển thành sản phẩm thương mại; website/backend/database hiện tại phục vụ phát triển và kiểm chứng AI.
