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
        # DOCX không có số trang ổn định; MVP chỉ trích xuất paragraphs.
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if sum(len(p) for p in paragraphs) > 2_000_000:
            raise ValueError("DOCUMENT_TOO_LONG")
        return [TextPage(None, normalize("\n".join(paragraphs)))]

    raise ValueError("Only PDF and DOCX are supported")


def make_chunks(
    pages: list[TextPage],
    size: int = 90,
    overlap: int = 20,
    min_words: int = 8,
) -> list[Chunk]:
    if not (0 <= overlap < size):
        raise ValueError("overlap must be smaller than size")

    result: list[Chunk] = []
    chunk_no = 0
    for page in pages:
        # Offset thuộc văn bản đã normalize, không phải tọa độ trên PDF.
        spans = list(re.finditer(r"\S+", page.text))
        for first in range(0, len(spans), size - overlap):
            last = min(first + size, len(spans))
            if last - first < min_words:
                break
            start = spans[first].start()
            end = spans[last - 1].end()
            result.append(
                Chunk(
                    chunk_no=chunk_no,
                    page_number=page.page_number,
                    start_offset=start,
                    end_offset=end,
                    text=page.text[start:end],
                )
            )
            chunk_no += 1
            if last == len(spans):
                break
    return result

