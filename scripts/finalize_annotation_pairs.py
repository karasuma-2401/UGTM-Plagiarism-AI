"""Validate approved annotation tasks and create the classifier training CSV."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


REQUIRED_INPUT_COLUMNS = {
    "task_id",
    "group_id",
    "query_text",
    "source_text",
    "reviewer_1_label",
    "reviewer_2_label",
    "final_label",
    "status",
}
OUTPUT_FIELDS = ("pair_id", "group_id", "label", "query_text", "source_text")


def label(value: str | None) -> str:
    return (value or "").strip()


def approved_label(row: dict[str, str], require_two_reviewers: bool) -> str:
    final = label(row.get("final_label"))
    reviewer_1 = label(row.get("reviewer_1_label"))
    reviewer_2 = label(row.get("reviewer_2_label"))
    status = label(row.get("status")).casefold()
    task_id = row.get("task_id", "<unknown>")

    if status != "approved":
        raise ValueError(f"{task_id}: status must be approved")
    if final not in {"0", "1"}:
        raise ValueError(f"{task_id}: final_label must be 0 or 1")
    if require_two_reviewers and (reviewer_1, reviewer_2) != (final, final):
        raise ValueError(f"{task_id}: two reviewers must agree with final_label")
    return final


def finalize_rows(
    rows: list[dict[str, str]], require_two_reviewers: bool
) -> list[dict[str, str]]:
    if not rows:
        raise ValueError("No annotation rows found")
    missing_columns = REQUIRED_INPUT_COLUMNS - set(rows[0])
    if missing_columns:
        raise ValueError(f"Missing columns: {sorted(missing_columns)}")

    result: list[dict[str, str]] = []
    errors: list[str] = []
    seen_task_ids: set[str] = set()
    for row in rows:
        task_id = label(row.get("task_id"))
        try:
            if not task_id:
                raise ValueError("task_id is required")
            if task_id in seen_task_ids:
                raise ValueError(f"{task_id}: duplicate task_id")
            seen_task_ids.add(task_id)
            if not label(row.get("group_id")):
                raise ValueError(f"{task_id}: group_id is required")
            if not label(row.get("query_text")) or not label(row.get("source_text")):
                raise ValueError(f"{task_id}: passage text is required")
            result.append(
                {
                    "pair_id": task_id,
                    "group_id": label(row.get("group_id")),
                    "label": approved_label(row, require_two_reviewers),
                    "query_text": label(row.get("query_text")),
                    "source_text": label(row.get("source_text")),
                }
            )
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("Annotation validation failed:\n- " + "\n- ".join(errors[:20]))
    if len({row["group_id"] for row in result}) < 6:
        raise ValueError("Need at least six independent group_id values")
    label_counts = Counter(row["label"] for row in result)
    if set(label_counts) != {"0", "1"}:
        raise ValueError("Need approved examples for both label 0 and label 1")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-two-reviewers", action="store_true")
    args = parser.parse_args()

    with args.input.open(newline="", encoding="utf-8") as source:
        finalized = finalize_rows(
            list(csv.DictReader(source)), args.require_two_reviewers
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(finalized)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "approved_pairs": len(finalized),
                "label_counts": dict(Counter(row["label"] for row in finalized)),
                "groups": len({row["group_id"] for row in finalized}),
            }
        )
    )


if __name__ == "__main__":
    main()
