export interface ClientConfig {
  api_key: string;
  default_corpus: string;
  embedding_model: string;
  reranker_model: string;
  classifier_enabled: boolean;
  max_upload_mb: number;
  remote_gpu_url?: string;
  remote_gpu_enabled: boolean;
}

export interface JobRecord {
  job_id: string;
  job_type: 'scan' | 'index';
  corpus_id: string;
  document_key: string;
  version: number;
  filename: string;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  error_code?: string;
  created_at?: string;
  updated_at?: string;
}

export interface FindingMatch {
  finding_id: string;
  submission_chunk_no: number;
  submission_page: number;
  submission_start_offset: number;
  submission_end_offset: number;
  submission_text: string;
  source_key: string;
  source_version: number;
  source_chunk_no: number;
  source_page: number;
  source_start_offset: number;
  source_end_offset: number;
  source_text: string;
  dense_score: number;
  lexical_score: number;
  rerank_score: number;
  classifier_score?: number;
  decision_source: 'heuristic' | 'classifier' | string;
  review_required: boolean;
}

export interface PassageGroup {
  group_id: string;
  source_key: string;
  source_version: number;
  submission_start_chunk: number;
  submission_end_chunk: number;
  findings: FindingMatch[];
  highest_rerank_score: number;
  mean_rerank_score: number;
  review_required: boolean;
}

export interface ReportData {
  submission_id: string;
  corpus_id: string;
  version: number;
  filename: string;
  total_chunks: number;
  flagged_chunks_count: number;
  max_rerank_score: number;
  mean_rerank_score: number;
  detector_mode: string;
  classifier_version?: string;
  passage_groups: PassageGroup[];
  isolated_findings: FindingMatch[];
  matched_sources_count: number;
  disclaimer: string;
}

export interface CorpusDocument {
  source_key: string;
  version: number;
  filename: string;
  is_active: boolean;
  chunk_count: number;
  indexed_at?: string;
}

export type ActiveTab = 'scan' | 'corpus' | 'report' | 'history' | 'system';

