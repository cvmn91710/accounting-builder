"""Extract text from PDFs: pdfplumber first, layout text + line-based tables, OCR fallback."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pdfplumber
import pytesseract
from pdf2image import convert_from_path
from PIL import Image


@dataclass
class PageText:
    page_number: int  # 1-based
    text: str
    ocr_used: bool


@dataclass
class ExtractedDocument:
    pages: list[PageText]
    combined_text: str
    tables_summary: str


def _ocr_page_image(img: Image.Image) -> str:
    return pytesseract.image_to_string(img)


def _extract_tables_from_page(page: Any) -> list[list[list[Optional[str]]]]:
    """Try default table detection, then line-based (common for bank statement grids)."""
    strategies: list[dict[str, Any]] = [
        {},
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
        },
        {
            "vertical_strategy": "lines",
            "horizontal_strategy": "text",
            "intersection_tolerance": 5,
            "snap_tolerance": 3,
            "text_tolerance": 2,
        },
    ]
    best_tables: list[list[list[Optional[str]]]] = []
    best_row_count = 0
    for ts in strategies:
        try:
            tables = page.extract_tables(table_settings=ts) or []
        except Exception:
            continue
        total_rows = sum(len(t) for t in tables if t)
        if total_rows > best_row_count:
            best_row_count = total_rows
            best_tables = tables
    if best_tables:
        return best_tables
    try:
        return page.extract_tables() or []
    except Exception:
        return []


def _page_plain_text(page: Any) -> str:
    text = (page.extract_text() or "").strip()
    if len(text) < 120:
        try:
            layout = (page.extract_text(layout=True) or "").strip()
        except Exception:
            layout = ""
        if len(layout) > len(text):
            text = layout
    return text


def extract_pdf(path: Path, ocr_dpi: int = 200) -> ExtractedDocument:
    pages_out: list[PageText] = []
    table_chunks: list[str] = []

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = _page_plain_text(page)

            tables = _extract_tables_from_page(page)
            page_has_table = False
            for ti, table in enumerate(tables):
                if not table:
                    continue
                n_nonempty = sum(
                    1
                    for row in table
                    if row and any(str(c or "").strip() for c in row)
                )
                if n_nonempty < 2:
                    continue
                page_has_table = True
                rows = [" | ".join(str(c or "").strip() for c in row) for row in table]
                table_chunks.append(f"Page {i} table {ti + 1}:\n" + "\n".join(rows))

            ocr_used = False
            if len(text) < 80 and not page_has_table:
                try:
                    pil_pages = convert_from_path(
                        path, first_page=i, last_page=i, dpi=ocr_dpi
                    )
                    if pil_pages:
                        ocr_text = _ocr_page_image(pil_pages[0]).strip()
                        if len(ocr_text) > len(text):
                            text = ocr_text
                            ocr_used = True
                except Exception:
                    pass

            pages_out.append(PageText(page_number=i, text=text, ocr_used=ocr_used))

    combined = "\n\n--- Page break ---\n\n".join(
        f"[Page {p.page_number}]{' [OCR]' if p.ocr_used else ''}\n{p.text}"
        for p in pages_out
    )
    tables_summary = "\n\n".join(table_chunks) if table_chunks else ""
    return ExtractedDocument(
        pages=pages_out, combined_text=combined, tables_summary=tables_summary
    )
