from collections import defaultdict


def group_adjacent_findings(findings: list[dict]) -> list[dict]:
    # Cùng source + cùng version, và chunk của 2 tài liệu cùng tiến tới.
    # Với chunk overlap, group chỉ là tiện ích hiển thị, chưa hợp nhất
    # chính xác khoảng ký tự toàn văn giữa các trang.
    by_source = defaultdict(list)
    for f in findings:
        by_source[(f["source_key"], f["source_version"])].append(f)

    next_id = 1
    for group in by_source.values():
        group.sort(
            key=lambda x: (x["submission_chunk_no"], x["source_chunk_no"])
        )
        previous = None
        for f in group:
            contiguous = (
                previous is not None
                and 0 <= f["submission_chunk_no"] - previous["submission_chunk_no"] <= 1
                and 0 <= f["source_chunk_no"] - previous["source_chunk_no"] <= 1
            )
            if contiguous:
                f["passage_group_id"] = previous["passage_group_id"]
            else:
                f["passage_group_id"] = f"PG-{next_id:04d}"
                next_id += 1
            previous = f
    return findings

