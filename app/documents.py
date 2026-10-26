import difflib
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from docx import Document


@dataclass
class TextPage:
    page_number: int | None
    text: str
    section_type: str = "body"


@dataclass
class Chunk:
    chunk_no: int
    page_number: int | None
    start_offset: int
    end_offset: int
    text: str
    section_type: str = "body"


RX_COVER_HEADER = re.compile(
    r"^(?:đại học quốc gia|bộ giáo dục(?: và đào tạo)?|trường đại học|university of|vietnam national university)\b",
    re.I,
)
RX_COVER_KEYWORDS = re.compile(
    r"\b(?:khóa luận tốt nghiệp|khoá luận tốt nghiệp|báo cáo tốt nghiệp|đề tài\s*:|tên đề tài\b)|^đề tài\b",
    re.I,
)
RX_TOC_HEADER = re.compile(
    r"^(?:mục lục|danh mục (?:hình|bảng|biểu đồ|sơ đồ|các bảng|các hình))\b",
    re.I,
)
RX_TOC_DOTS = re.compile(r"(?:\.{4,}|…{2,}|\.\s*\.\s*\.\s*\.)")
RX_BIB_HEADER = re.compile(
    r"^(?:tài liệu tham khảo|tham khảo|references|bibliography)\b",
    re.I,
)
RX_ABBR_HEADER = re.compile(
    r"^(?:danh mục từ viết tắt|bảng từ viết tắt|các từ viết tắt|danh mục ký hiệu|bảng các ký hiệu|từ viết tắt)\b",
    re.I,
)
RX_TABLE_STRUCT = re.compile(
    r"(?:\bstt\b.{0,60}(?:kiểu dữ liệu|thuộc tính|ràng buộc|mô tả|diễn giải)|\bbảng\s+\d+(?:[.\-]\d+){0,3}\s*:\s*bảng\b)",
    re.I,
)
RX_TITLE = re.compile(
    r"(?:ĐỀ TÀI|Đề tài|ĐỀ TÀI:|Đề tài:)[\s\n]+(.*?)(?=\s+(?:Building|Developing|Applying|Researching|Design|Constructing|An |A |CỬ NHÂN|KỸ SƯ|GIẢNG VIÊN|GVHD|TP\.|THÀNH PHỐ|\d{4}|$))",
    re.I | re.DOTALL,
)
RX_TITLE_FALLBACK = re.compile(
    r"(?:KHÓA|KHOÁ) LUẬN TỐT NGHIỆP[\s\n]+(.*?)(?=\s+(?:Building|Developing|Applying|Researching|Design|Constructing|An |A |CỬ NHÂN|KỸ SƯ|GIẢNG VIÊN|GVHD|TP\.|THÀNH PHỐ|\d{4}|$))",
    re.I | re.DOTALL,
)
RX_AUTHORS = re.compile(
    r"(?:KHOA[^\n\r]+?)(?:[\s\n]+)((?:[A-ZÀ-Ỹ\s–\-\d]{4,90}))[\s\n]+(?:KHÓA|KHOÁ)\s+LUẬN",
    re.DOTALL,
)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def classify_text_section(text: str, page_number: int | None = None) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip()
    s_lower = s.casefold()
    if not s_lower:
        return "empty"

    if page_number is not None and page_number <= 2:
        if (
            RX_COVER_HEADER.search(s_lower[:200])
            or RX_COVER_KEYWORDS.search(s_lower[:250])
            or ("khoa công nghệ phần mềm" in s_lower[:250] and "khóa luận" in s_lower)
        ):
            return "cover"

    if RX_COVER_HEADER.search(s_lower[:150]):
        return "cover"
    if RX_ABBR_HEADER.search(s_lower[:120]):
        return "abbreviations"
    if RX_BIB_HEADER.search(s_lower[:120]) or (
        len(re.findall(r"\[\s*\d+\s*\]", s)) >= 3
        and len(re.findall(r"https?://|\baccessed\b|\bavailable\b", s_lower)) >= 2
    ):
        return "references"
    if RX_TOC_HEADER.search(s_lower[:120]) or (
        len(RX_TOC_DOTS.findall(s)) >= 2
        and re.search(r"\b(?:bảng|hình|use\s*case|mục|chương)\b", s_lower)
    ):
        return "table_of_contents"
    if RX_TABLE_STRUCT.search(s_lower[:350]):
        return "structured_table"
    return "body"


def extract_pages(path: str) -> list[TextPage]:
    file = Path(path)
    if file.suffix.lower() == ".pdf":
        with pymupdf.open(str(file)) as pdf:
            if len(pdf) > 250:
                raise ValueError("TOO_MANY_PAGES")
            pages: list[TextPage] = []
            for i, page in enumerate(pdf):
                page_num = i + 1
                norm_text = normalize(page.get_text("text", sort=True))
                section_type = classify_text_section(norm_text, page_number=page_num)
                pages.append(
                    TextPage(
                        page_number=page_num,
                        text=norm_text,
                        section_type=section_type,
                    )
                )
            return pages

    if file.suffix.lower() == ".docx":
        doc = Document(str(file))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if sum(len(p) for p in paragraphs) > 2_000_000:
            raise ValueError("DOCUMENT_TOO_LONG")
        norm_text = normalize("\n".join(paragraphs))
        section_type = classify_text_section(norm_text, page_number=1)
        return [TextPage(None, norm_text, section_type=section_type)]

    raise ValueError("Only PDF and DOCX are supported")


def make_chunks(
    pages: list[TextPage],
    size: int = 90,
    overlap: int = 20,
    min_words: int = 8,
    exclude_sections: set[str] | tuple[str, ...] | None = None,
) -> list[Chunk]:
    if not (0 <= overlap < size):
        raise ValueError("overlap must be smaller than size")

    excluded = set(exclude_sections) if exclude_sections else set()
    result: list[Chunk] = []
    chunk_no = 0
    for page in pages:
        spans = list(re.finditer(r"\S+", page.text))
        for first in range(0, len(spans), size - overlap):
            last = min(first + size, len(spans))
            if last - first < min_words:
                break
            start = spans[first].start()
            end = spans[last - 1].end()
            chunk_text = page.text[start:end]

            chunk_section = page.section_type
            if chunk_section == "body" and page.page_number is None:
                detected = classify_text_section(chunk_text, page_number=None)
                if detected != "body":
                    chunk_section = detected

            if chunk_section in excluded:
                continue

            result.append(
                Chunk(
                    chunk_no=chunk_no,
                    page_number=page.page_number,
                    start_offset=start,
                    end_offset=end,
                    text=chunk_text,
                    section_type=chunk_section,
                )
            )
            chunk_no += 1
            if last == len(spans):
                break
    return result


def extract_document_metadata(
    path: str | Path,
    pages: list[TextPage] | None = None,
) -> dict[str, str]:
    path = Path(path)
    if pages is None:
        if path.suffix.lower() == ".pdf":
            with pymupdf.open(str(path)) as pdf:
                pages = [TextPage(1, normalize(pdf[0].get_text("text", sort=True)))] if len(pdf) > 0 else []
        elif path.suffix.lower() == ".docx":
            doc = Document(str(path))
            paras = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
            pages = [TextPage(None, normalize(" ".join(paras[:25])))]
        else:
            pages = []
    full_text = " ".join(p.text for p in pages)
    cover = pages[0].text[:1500] if pages else ""

    m_title = RX_TITLE.search(cover) or RX_TITLE_FALLBACK.search(cover)
    title = m_title.group(1).strip() if m_title else ""

    m_authors = RX_AUTHORS.search(cover)
    authors = m_authors.group(1).strip() if m_authors else ""
    authors = re.sub(r"[\d–\-]", "", authors).strip()

    def norm_field(s: str) -> str:
        s_clean = unicodedata.normalize("NFKC", s or "").casefold()
        s_clean = re.sub(r"[^\w\s]", " ", s_clean)
        return re.sub(r"\s+", " ", s_clean).strip()

    norm_title = norm_field(title)
    norm_authors = norm_field(authors)
    cover_prefix = norm_field(cover[:350])
    text_sha256 = hashlib.sha256(full_text.encode("utf-8")).hexdigest()

    return {
        "title": title[:150],
        "norm_title": norm_title,
        "authors": authors[:100],
        "norm_authors": norm_authors,
        "cover_prefix": cover_prefix,
        "text_sha256": text_sha256,
    }


def is_duplicate_document(
    meta1: dict[str, str], meta2: dict[str, str]
) -> tuple[bool, str]:
    if meta1.get("text_sha256") and meta1["text_sha256"] == meta2.get("text_sha256"):
        return True, "SAME_TEXT_HASH"

    t1 = meta1.get("norm_title", "")
    t2 = meta2.get("norm_title", "")
    if len(t1) > 15 and len(t2) > 15:
        t_ratio = difflib.SequenceMatcher(None, t1, t2).ratio()
        if t_ratio >= 0.85:
            a1 = meta1.get("norm_authors", "")
            a2 = meta2.get("norm_authors", "")
            if a1 and a2:
                a_ratio = difflib.SequenceMatcher(None, a1, a2).ratio()
                if a_ratio >= 0.75:
                    return True, f"TITLE_AND_AUTHORS_MATCH (title={t_ratio:.2f}, authors={a_ratio:.2f})"
            else:
                c1 = meta1.get("cover_prefix", "")
                c2 = meta2.get("cover_prefix", "")
                c_ratio = difflib.SequenceMatcher(None, c1, c2).ratio() if (c1 and c2) else 0.0
                if c_ratio >= 0.80:
                    return True, f"TITLE_AND_COVER_MATCH (title={t_ratio:.2f}, cover={c_ratio:.2f})"
    return False, ""
