import React, { useState } from 'react';
import { Download, RefreshCw, CheckCircle2, Split, Library, FileWarning, Search, AlertTriangle } from 'lucide-react';
import { FindingMatch, PassageGroup, ReportData } from '../types';

interface ReportTabProps {
  report: ReportData | null;
  isLoading: boolean;
  onRefreshLatest: () => void;
  showToast: (msg: string, type: 'success' | 'error' | 'info' | 'warning') => void;
}

export const ReportTab: React.FC<ReportTabProps> = ({
  report,
  isLoading,
  onRefreshLatest,
  showToast,
}) => {
  const [filterSource, setFilterSource] = useState<string>('all');
  const [minScore, setMinScore] = useState<number>(0);
  const [searchQuery, setSearchQuery] = useState<string>('');

  const handleDownloadJson = () => {
    if (!report) {
      showToast('Chưa có báo cáo để xuất!', 'warning');
      return;
    }
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `UGTM_Report_${report.submission_id || 'scan'}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('Đã tải xuống file báo cáo JSON!', 'success');
  };

  if (isLoading) {
    return (
      <div className="bg-white rounded-3xl border border-slate-200 p-16 text-center space-y-4">
        <RefreshCw className="w-10 h-10 animate-spin text-[#2054A5] mx-auto" />
        <p className="text-sm font-semibold text-slate-700">Đang nạp dữ liệu báo cáo thẩm định...</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="bg-white rounded-3xl border border-slate-200 p-16 text-center space-y-4">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 flex items-center justify-center mx-auto text-slate-400">
          <Split className="w-7 h-7" />
        </div>
        <div>
          <h3 className="text-base font-bold text-slate-800">Chưa có báo cáo phân tích nào được mở</h3>
          <p className="text-xs text-slate-500 max-w-md mx-auto mt-1">
            Vui lòng thực hiện một tác vụ quét trong tab <strong>"Quét bài nộp"</strong> hoặc chọn một tác vụ đã hoàn thành trong tab <strong>"Lịch sử tác vụ"</strong> để đối chiếu.
          </p>
        </div>
        <button
          onClick={onRefreshLatest}
          className="inline-flex items-center space-x-2 px-4 py-2 bg-blue-50 text-[#2054A5] hover:bg-blue-100 rounded-xl text-xs font-bold transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Tải Báo Cáo Gần Nhất</span>
        </button>
      </div>
    );
  }

  // Calculate highest score
  const maxScorePercent = Math.round((report.max_rerank_score || 0) * 100);
  const totalFindings =
    (report.passage_groups?.reduce((acc, g) => acc + (g.findings?.length || 0), 0) || 0) +
    (report.isolated_findings?.length || 0);

  // Group items
  const allGroups: PassageGroup[] = report.passage_groups || [];

  // Filter sources list
  const uniqueSources = Array.from(
    new Set(allGroups.map((g) => g.source_key).filter(Boolean))
  );

  // Filtered groups
  const filteredGroups = allGroups.filter((group) => {
    if (filterSource !== 'all' && group.source_key !== filterSource) return false;
    const groupScore = Math.round((group.highest_rerank_score || 0) * 100);
    if (groupScore < minScore) return false;

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      const hasMatch = group.findings?.some(
        (f) =>
          f.submission_text?.toLowerCase().includes(q) ||
          f.source_text?.toLowerCase().includes(q) ||
          f.source_key?.toLowerCase().includes(q)
      );
      if (!hasMatch) return false;
    }

    return true;
  });

  return (
    <div className="space-y-6">
      {/* Top Header & Overview Cards */}
      <div className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 sm:p-7 space-y-6">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-slate-100">
          <div>
            <div className="flex items-center space-x-2">
              <span className="text-xs font-semibold px-2.5 py-0.5 rounded-full bg-blue-100 text-blue-800">
                Báo Cáo Thẩm Định
              </span>
              <h1 className="text-xl font-extrabold text-slate-900 font-mono">{report.submission_id}</h1>
            </div>
            <p className="text-xs text-slate-500 mt-1">
              File: <strong className="text-slate-700">{report.filename}</strong> (v{report.version}) | Chế độ AI:{' '}
              <span className="font-semibold text-slate-700">{report.detector_mode}</span>
              {report.classifier_version && (
                <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-purple-100 text-purple-700 font-mono">
                  {report.classifier_version}
                </span>
              )}
            </p>
          </div>

          <div className="flex items-center space-x-2">
            <button
              onClick={handleDownloadJson}
              className="inline-flex items-center space-x-2 px-3.5 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-semibold transition-all"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Xuất JSON</span>
            </button>
            <button
              onClick={onRefreshLatest}
              className="inline-flex items-center space-x-2 px-4 py-2 rounded-xl bg-[#2054A5] hover:bg-[#184285] text-white text-xs font-bold shadow-sm transition-all"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Tải Mới Nhất</span>
            </button>
          </div>
        </div>

        {/* 4 Overview Metric Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {/* Similarity Rate Card */}
          <div
            className={`p-4 rounded-2xl border transition-all ${
              maxScorePercent === 0
                ? 'bg-emerald-50/70 border-emerald-200'
                : maxScorePercent >= 70
                ? 'bg-red-50/70 border-red-200'
                : maxScorePercent >= 40
                ? 'bg-orange-50/70 border-orange-200'
                : 'bg-amber-50/70 border-amber-200'
            }`}
          >
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Tương đồng cao nhất</p>
            <div className="flex items-baseline space-x-2 mt-1">
              <span
                className={`text-2xl sm:text-3xl font-extrabold ${
                  maxScorePercent === 0
                    ? 'text-emerald-700'
                    : maxScorePercent >= 70
                    ? 'text-red-700'
                    : maxScorePercent >= 40
                    ? 'text-orange-700'
                    : 'text-amber-700'
                }`}
              >
                {maxScorePercent}%
              </span>
            </div>
            <p className="text-[11px] font-medium text-slate-600 mt-1">
              {maxScorePercent === 0
                ? 'Không phát hiện trùng lặp'
                : maxScorePercent >= 70
                ? 'Trùng lặp cao (Nghi vấn sao chép)'
                : 'Trùng lặp trung bình'}
            </p>
          </div>

          {/* Suspicious Groups */}
          <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200/80">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Số đoạn nghi vấn</p>
            <p className="text-2xl sm:text-3xl font-extrabold text-slate-800 mt-1">{report.passage_groups?.length || 0}</p>
            <p className="text-[11px] text-slate-400 mt-1">{totalFindings} Cặp văn bản chi tiết</p>
          </div>

          {/* Matched Sources */}
          <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200/80">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Nguồn trùng lặp</p>
            <p className="text-2xl sm:text-3xl font-extrabold text-slate-800 mt-1">{report.matched_sources_count || 0}</p>
            <p className="text-[11px] text-slate-400 mt-1">Tài liệu trong kho tham chiếu</p>
          </div>

          {/* Analyzed Chunks */}
          <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200/80">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Đoạn đã phân tích</p>
            <p className="text-2xl sm:text-3xl font-extrabold text-slate-800 mt-1">{report.total_chunks || 0}</p>
            <p className="text-[11px] text-slate-400 mt-1">BGE-M3 Dense + Lexical</p>
          </div>
        </div>
      </div>

      {/* Filter and Search Bar */}
      {maxScorePercent > 0 && (
        <div className="bg-white rounded-2xl border border-slate-200 p-4 flex flex-wrap items-center justify-between gap-3 text-xs">
          <div className="flex flex-wrap items-center gap-3 flex-1">
            {/* Search input */}
            <div className="relative min-w-[200px] flex-1 max-w-sm">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Tìm từ khóa trong nội dung đối chiếu..."
                className="w-full pl-9 pr-3 py-2 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500"
              />
            </div>

            {/* Source selector */}
            <select
              value={filterSource}
              onChange={(e) => setFilterSource(e.target.value)}
              className="px-3 py-2 rounded-xl border border-slate-200 focus:outline-none focus:ring-2 focus:ring-blue-500/20 text-slate-700 font-medium"
            >
              <option value="all">Tất cả tài liệu nguồn ({uniqueSources.length})</option>
              {uniqueSources.map((s) => (
                <option key={s} value={s}>
                  Nguồn: {s}
                </option>
              ))}
            </select>

            {/* Threshold filter */}
            <div className="flex items-center space-x-2 text-slate-600">
              <span>Ngưỡng &gt;=</span>
              <input
                type="range"
                min="0"
                max="90"
                step="5"
                value={minScore}
                onChange={(e) => setMinScore(parseInt(e.target.value))}
                className="w-24 accent-[#2054A5]"
              />
              <span className="font-mono font-bold text-[#2054A5]">{minScore}%</span>
            </div>
          </div>

          <span className="text-slate-500 text-[11px]">
            Hiển thị <strong>{filteredGroups.length}</strong> / {allGroups.length} nhóm đoạn
          </span>
        </div>
      )}

      {/* Side-by-Side Findings Section */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-slate-800 flex items-center space-x-2">
            <Split className="w-4 h-4 text-[#2054A5]" />
            <span>Chi tiết Đối chiếu Từng Đoạn Vi Phạm (Side-by-Side Comparison)</span>
          </h2>
          <span className="text-xs text-slate-500 font-medium">
            <span className="inline-block w-2.5 h-2.5 rounded-full bg-orange-400 mr-1" />
            Màu cam: Bài nộp nghi vấn | <span className="inline-block w-2.5 h-2.5 rounded-full bg-blue-500 mr-1" />
            Màu xanh: Văn bản gốc đối chứng
          </span>
        </div>

        {/* Zero Violations View */}
        {maxScorePercent === 0 && (
          <div className="bg-white rounded-3xl border border-slate-200 p-12 text-center space-y-3">
            <div className="w-16 h-16 rounded-full bg-emerald-50 text-emerald-600 flex items-center justify-center mx-auto shadow-sm">
              <CheckCircle2 className="w-10 h-10 stroke-[2.2]" />
            </div>
            <h3 className="text-base font-bold text-slate-800">Không tìm thấy đoạn văn nào vi phạm ngưỡng trùng lặp</h3>
            <p className="text-xs text-slate-500 max-w-lg mx-auto leading-relaxed">
              Tài liệu đã được đối chiếu toàn văn với toàn bộ bài trong kho tham chiếu nhưng không phát hiện dấu hiệu đạo văn hoặc sao chép vượt ngưỡng.
            </p>
          </div>
        )}

        {/* Group Cards List */}
        {filteredGroups.map((group, gIdx) => {
          const groupScorePercent = Math.round((group.highest_rerank_score || 0) * 100);

          return (
            <div
              key={group.group_id || gIdx}
              className="bg-white rounded-3xl border border-slate-200 overflow-hidden shadow-sm hover:shadow-md transition-shadow"
            >
              {/* Card Header Bar */}
              <div className="bg-slate-50/80 px-6 py-3 border-b border-slate-200/80 flex flex-wrap items-center justify-between gap-2 text-xs">
                <div className="flex items-center space-x-2.5">
                  <span className="w-6 h-6 rounded-lg bg-red-600 text-white font-mono font-bold text-[11px] flex items-center justify-center">
                    #{gIdx + 1}
                  </span>
                  <span className="font-bold text-slate-800 font-mono">Nhóm: {group.group_id}</span>
                  <span className="text-slate-400">|</span>
                  <span className="text-slate-600">
                    Đối chiếu nguồn: <strong className="font-mono text-blue-700">{group.source_key}</strong> (v{group.source_version})
                  </span>
                </div>

                <div className="flex items-center space-x-2">
                  <span className="px-2.5 py-1 rounded-full text-xs font-extrabold bg-red-50 text-red-700 border border-red-200">
                    Rerank Score: {(group.highest_rerank_score || 0).toFixed(4)} ({groupScorePercent}%)
                  </span>
                  {group.findings?.[0]?.decision_source === 'classifier' ? (
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-purple-100 text-purple-800 border border-purple-200">
                      CUSTOM_CLASSIFIER
                    </span>
                  ) : (
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-slate-200 text-slate-700">
                      HEURISTIC
                    </span>
                  )}
                </div>
              </div>

              {/* Sub-findings list inside group */}
              <div className="divide-y divide-slate-100">
                {group.findings?.map((finding: FindingMatch, fIdx: number) => {
                  const rerankPercent = Math.round((finding.rerank_score || 0) * 100);

                  return (
                    <div
                      key={finding.finding_id || fIdx}
                      className="grid grid-cols-1 md:grid-cols-12 text-xs divide-y md:divide-y-0 md:divide-x divide-slate-200"
                    >
                      {/* Left: Suspect Submission Text */}
                      <div className="md:col-span-5 p-5 bg-orange-50/20 space-y-2">
                        <div className="flex items-center justify-between text-orange-950 font-semibold border-b border-orange-200/50 pb-1.5">
                          <span className="flex items-center space-x-1.5">
                            <FileWarning className="w-3.5 h-3.5 text-orange-600" />
                            <span>Nội dung bài nộp nghi vấn</span>
                          </span>
                          <span className="text-[10px] text-orange-700 font-mono">
                            Đoạn #{finding.submission_chunk_no} {finding.submission_page ? `(Trang ${finding.submission_page})` : ''}
                          </span>
                        </div>
                        <div className="p-3.5 bg-white rounded-2xl border border-orange-200/80 text-slate-800 leading-relaxed font-sans shadow-sm custom-scrollbar max-h-60 overflow-y-auto">
                          <span className="bg-orange-100/90 text-orange-950 px-1 py-0.5 rounded">
                            {finding.submission_text}
                          </span>
                        </div>
                      </div>

                      {/* Center: AI Metric Pillar */}
                      <div className="md:col-span-2 p-4 bg-slate-50/80 flex flex-col justify-center items-center text-center space-y-3">
                        <div className="w-full">
                          <p className="text-[10px] text-slate-500 font-bold uppercase tracking-wider">BGE Cross-Encoder</p>
                          <p className="text-base font-extrabold text-red-600 mt-0.5">{rerankPercent}%</p>
                          <div className="w-full bg-slate-200 rounded-full h-1.5 mt-1 overflow-hidden">
                            <div className="bg-red-500 h-full rounded-full" style={{ width: `${rerankPercent}%` }} />
                          </div>
                        </div>

                        <div className="w-full pt-2 border-t border-slate-200/70 text-[10px] space-y-1 text-slate-600">
                          <div className="flex justify-between">
                            <span className="text-slate-500">Vector Cosine:</span>
                            <span className="font-mono font-bold text-slate-800">{finding.dense_score?.toFixed(4)}</span>
                          </div>
                          <div className="flex justify-between">
                            <span className="text-slate-500">Trigram Lexical:</span>
                            <span className="font-mono font-bold text-slate-800">{finding.lexical_score?.toFixed(4)}</span>
                          </div>
                        </div>
                      </div>

                      {/* Right: Source Document Text */}
                      <div className="md:col-span-5 p-5 bg-blue-50/20 space-y-2">
                        <div className="flex items-center justify-between text-blue-950 font-semibold border-b border-blue-200/50 pb-1.5">
                          <span className="flex items-center space-x-1.5">
                            <Library className="w-3.5 h-3.5 text-blue-600" />
                            <span>Văn bản gốc trong kho tham chiếu</span>
                          </span>
                          <span className="text-[10px] text-blue-700 font-mono">
                            Đoạn #{finding.source_chunk_no} {finding.source_page ? `(Trang ${finding.source_page})` : ''}
                          </span>
                        </div>
                        <div className="p-3.5 bg-white rounded-2xl border border-blue-200/80 text-slate-800 leading-relaxed font-sans shadow-sm custom-scrollbar max-h-60 overflow-y-auto">
                          <span className="bg-blue-100/90 text-blue-950 px-1 py-0.5 rounded">
                            {finding.source_text}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>

      {/* Academic Disclaimer Box */}
      <div className="bg-amber-50/80 border border-amber-200 rounded-2xl p-4 sm:p-5 flex items-start space-x-3.5 text-xs text-amber-900 shadow-sm">
        <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0 mt-0.5" />
        <div className="space-y-1">
          <strong className="font-bold text-amber-950">Lưu ý nghiệp vụ học thuật & quy chế:</strong>
          <p className="leading-relaxed text-amber-800">
            {report.disclaimer ||
              'Các chỉ số tương đồng và đoạn văn trùng lặp là bằng chứng kỹ thuật phục vụ hội đồng chấm và giảng viên hướng dẫn thẩm định ngữ cảnh, trích dẫn; hệ thống không tự động đưa ra kết luận kỷ luật đạo văn.'}
          </p>
        </div>
      </div>
    </div>
  );
};
