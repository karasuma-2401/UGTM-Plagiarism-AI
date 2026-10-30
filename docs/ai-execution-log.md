# UGTM-Plagiarism-AI — Nhật ký Thực thi & Giao thức Kiểm soát Thay đổi (`CP-00.3`)

- **Người thực hiện:** Solo Developer (cùng AI Pair Programmer)
- **Khung thời gian tham chiếu:** `30/10/2026 – 10/12/2026` (6 tuần; ước lượng `240h` thực thi + `30h` dự phòng = `270h`)
- **Sổ đăng ký thí nghiệm (Machine-readable):** [`configs/experiment_registry.json`](../configs/experiment_registry.json)
- **Checklist chính thức:** [`docs/ai-development-checkpoints.md`](./ai-development-checkpoints.md)

---

## 1. Phân định Phạm vi (Scope Charter)

### 1.1. Scope AI Bắt buộc (Core Mandatory Scope — Không cắt giảm)
1. **Hạ tầng phát triển thực tế (`CP-01`):** Tách CPU/API khỏi GPU, Neon PostgreSQL (`pgvector` + `pg_trgm`) có migration, queue Celery/Broker rõ ràng, shared storage adapter, Colab Remote GPU Worker có auth + pin model revision + không fallback CPU âm thầm, xóa lộ API key public.
2. **Tiền xử lý, Corpus & Bằng chứng (`CP-02`):** Block-level parser cho PDF & DOCX giữ ánh xạ vị trí, thống nhất section filtering và sentence/paragraph chunking giữa offline và online, dedup & phân quyền corpus manifest, chuẩn hóa Report Schema `v3` kèm provenance và ngữ nghĩa `coverage`.
3. **Benchmark & Evaluator tái lập (`CP-03`):** Annotation guideline minh bạch (`single-annotator` hoặc ghi đúng provenance nếu có người kiểm định độc lập), mục tiêu `600–1.000` cặp / `40–60` document families / `60–100` query documents đủ các slice (copy, paraphrase, summarization, multi-source, VI–VI, VI–EN/EN–VI, hard negatives, boilerplate, có trích dẫn, nguồn ngoài kho), khóa split theo family chống leakage, evaluator đa tầng (`Recall@K`, `nDCG@10`, span `F1`/`IoU`, `PR-AUC`, calibration, false alarms/bài, latency) và chạy `B0–B4`.
4. **Nâng cấp AI cốt lõi & Ablation (`CP-04`):** Tối ưu retrieval bằng đo lường, sentence/span alignment, citation-aware context (`present/absent/uncertain` độc lập với nhãn reuse), đánh giá classifier + calibration + chính sách review, chạy robustness/ablation đầy đủ trên từng slice và freeze phương án AI chính (`RC1`).
5. **Độ bền hệ thống & Khả năng tích hợp UGTM (`CP-05`):** Vòng đời job chịu lỗi khi Colab GPU ngắt kết nối (`waiting_for_compute`/resume/idempotency), xử lý khoảng trống DB commit–enqueue, Evidence Workbench khớp Schema `v3`, kiểm chứng contract & RBAC với `karasuma-2401/UGTM`, backup/restore và đánh giá thao tác xem bằng chứng trung thực.
6. **Đánh giá cuối & Hồ sơ bàn giao (`CP-06`):** Chạy tập `test` đã khóa kèm `95%` bootstrap CI theo family, đóng gói reproducibility, báo cáo nghiên cứu truy vết `claim → experiment`, báo cáo mức sẵn sàng kỹ thuật (`docs/technical-readiness.md`) và kịch bản demo thực tế.

### 1.2. Scope Mở rộng Có Điều kiện (Conditional Extensions — Chỉ chạy sau khi xong `CP-04`)
- **`EXT-01`:** So sánh có đối chứng với `Qwen3-Embedding-0.6B` hoặc nhánh sparse của `BAAI/bge-m3`.
- **`EXT-02`:** Định tuyến OCR có chọn lọc cho trang PDF scan/thiếu text layer, giữ tọa độ và đo `CER`/span quality.
- **`EXT-03`:** Fine-tune có kiểm soát (LoRA / hard negatives) chỉ khi đủ nhãn độc lập và error analysis chứng minh domain gap.
- *(Các hướng dài hạn ngoài phạm vi 6 tuần: đạo văn hình ảnh/biểu đồ, đạo văn mã nguồn, tìm kiếm toàn Internet, phát hiện văn bản do AI sinh).*

### 1.3. Phạm vi Loại bỏ (Explicitly Excluded Business Scope)
- **Loại bỏ hoàn toàn:** nghiên cứu thị trường, tìm khách hàng, định giá gói cước, cổng thanh toán/billing, cam kết SLA thương mại trên hạ tầng dev (Render Free / Colab), thiết kế lại website nghiệp vụ UGTM hoặc xây nền tảng SaaS multi-tenant.

---

## 2. Quy hoạch Lưu trữ Artifacts & Quy tắc Bảo mật Dữ liệu

| Đường dẫn | Trạng thái Git | Mục đích & Quy tắc an toàn |
|---|---|---|
| `configs/` | **Tracked** | Lưu cấu hình baseline (`baseline_b0.json`), cấu hình thí nghiệm, model/parser revisions và `experiment_registry.json`. Tuyệt đối không chứa secret. |
| `docs/` & `docs/contracts/` | **Tracked** | Lưu tài liệu kiến trúc, audit report, hợp đồng tích hợp UGTM, JSON contract mẫu (dùng văn bản mẫu/synthetic hợp lệ) và execution log. |
| `artifacts/*.manifest.json` | **Tracked** | Lưu metadata/provenance của classifier đã huấn luyện (version, schema, hashes, metrics). |
| `artifacts/*.joblib` | **Git-ignored** | Trọng số nhị phân của classifier; đồng bộ qua shared object storage / release bundle có kiểm soát SHA-256. |
| `data/baseline_b0/` | **Git-ignored** | Lưu snapshot dự đoán B0, PDF mẫu sinh phục vụ kiểm định và report đối chứng B0. |
| `corpus_staging/` & `data/` | **Git-ignored** | Chứa tệp khóa luận staging và `corpus_manifest.csv`. Chỉ xử lý trên cloud (Neon/Colab/Storage) đối với các tài liệu được xác nhận quyền ở `CP-02.3`. |
| `training_data/` & `training_runs/` | **Git-ignored** | Chứa cặp gán nhãn, splits, predictions từng run và cache embeddings/reranker scores theo `(content_hash, model_revision, config_hash)`. |

---

## 3. Giao thức Thay đổi & Các Mốc Đóng băng (Change Control & Freeze Gates)

1. **Truy vết thay đổi (`Traceability`):** Mọi thay đổi đối với `parser`, `section filter`, `chunker`, `top_k / fused_k / prefilter`, `alignment`, `citation detector`, `classifier` hoặc `threshold` đều phải:
   - Tham chiếu `Task ID` (`CP-xx.y`) và `Experiment ID` trong [`configs/experiment_registry.json`](../configs/experiment_registry.json).
   - Ghi rõ lý do thay đổi, commit/config hash, tập dữ liệu đánh giá (`dev` vs `test`) và sự thay đổi metric trong bảng nhật ký dưới đây.
2. **Bảo vệ tập Test (`Zero Test Leakage`):** Sau mốc `FREEZE-BENCHMARK-SPLIT` (`CP-03.3`), tập `test` bị khóa hoàn toàn; mọi việc chọn model, chọn `K`, chỉnh regex lọc hoặc chọn ngưỡng quyết định chỉ được thực hiện trên `train`/`dev`. Nếu phát hiện lỗi sau khi đã mở `test` ở `CP-06.1`, mọi chỉnh sửa tiếp theo phải khai báo rõ `post-test modification`.
3. **Trung thực về Bằng chứng (`Evidence Integrity`):** Luôn gắn nhãn rõ ràng cho mọi con số:
   - `[MEASURED]`: đo thực tế từ code + dữ liệu xác định, tái chạy ra cùng kết quả.
   - `[TARGET]`: mục tiêu thiết kế cần kiểm chứng (ví dụ `Recall@50 >= 0.90`).
   - `[SIMULATED / OFFLINE]`: kiểm thử hợp đồng hoặc chạy từng phần khi thành phần khác đang tắt.

---

## 4. Bảng Nhật ký Thực thi (Execution Log)

| Ngày | Task ID | Trạng thái | Việc đã làm | Bằng chứng / Commit / Artifact | Vướng mắc | Bước tiếp |
|---|---|---|---|---|---|---|
| `2026-10-30` | `CP-00.1` | `done` | Chốt commit gốc `78e4a94`; chạy `pytest` (`24/24 passed`); probe toàn bộ 11 module Python trên `.venv` (`Python 3.13.15`); kiểm toán 2 cặp classifier manifest/joblib (`dataset_size=24`, `6` cặp test synthetic) và thống kê triage `600` cặp; chạy thử DOCX mẫu + PDF mẫu 2 trang + tái hiện thực nghiệm lỗi DOCX mất `100%` thân bài khi lọc `cover` và hiện tượng lệch pha cửa sổ 90 từ. | Base commit `78e4a94f173a32f178bab70568d397625227533d`; [`configs/baseline_b0.json`](../configs/baseline_b0.json); [`docs/baseline-audit.md`](./baseline-audit.md); `data/baseline_b0/b0_audit_snapshot.json`; `data/baseline_b0/b0_sample_report_docx_schema_v2.json`; `data/baseline_b0/b0_sample_report_pdf_schema_v2.json`; `data/baseline_b0/b0_cover_bug_repro.json`. | Phát hiện `requests` thiếu trong `requirements.txt`; `.venv` chưa cài `celery`/`uvicorn`/`torch`/`FlagEmbedding`; `scikit-learn` lệch version `1.6.1` vs `1.9.1`; Docker/Postgres/GPU đang offline nên điểm dense/rerank B0 để `null` trung thực, sẽ đo full B0 ở `CP-01.6` & `CP-03.5`. | `CP-00.2` |
| `2026-10-30` | `CP-00.2` | `done` | Đối chiếu trực tiếp mã nguồn thực tế `karasuma-2401/UGTM@dev` (commit `799d6f91`, `.NET 10`, module `ThesisManagement`, `Identity`, `FinalReport`, `SubmissionFile`, `S3ThesisSubmissionObjectStorage`, `FinalReportReadAccessChecker`, `IThesisManagementAuditOutbox`); chốt ranh giới lưu trữ file gốc, RBAC xem báo cáo, ánh xạ ID/version/SHA-256, xử lý lệch định dạng `.doc` (`415`) và lộ trình schema `v2 → v3`. | [`docs/ai-integration-contract.md`](./ai-integration-contract.md); [`docs/contracts/scan_job_accepted.sample.json`](./contracts/scan_job_accepted.sample.json); [`docs/contracts/job_status.sample.json`](./contracts/job_status.sample.json); [`docs/contracts/report_v2.sample.json`](./contracts/report_v2.sample.json). | Không bị chặn; repo UGTM đã truy cập được công khai trên nhánh `dev` nên contract bám sát entity thật. | `CP-00.3` |
| `2026-10-30` | `CP-00.3` | `done` | Khởi tạo sổ nhật ký thực thi, quy chế phân định scope (bắt buộc / mở rộng có điều kiện / loại bỏ kinh doanh), quy hoạch thư mục artifacts bảo mật dữ liệu, 4 mốc freeze (`FREEZE-B0`, `FREEZE-SCHEMA-V3`, `FREEZE-BENCHMARK-SPLIT`, `FREEZE-AI-RC1`) và registry thí nghiệm `B0–B4`, `EXP-04.1..04.5`, `EXT-01..03`. | [`docs/ai-execution-log.md`](./ai-execution-log.md); [`configs/experiment_registry.json`](../configs/experiment_registry.json). | Không có. Đạt toàn bộ Exit Criteria của `CP-00` mà chưa chỉnh sửa dòng code thuật toán nào. | `CP-01.1` |

