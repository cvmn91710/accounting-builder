"""Extract text from PDFs: pdfplumber first, OCR fallback per page."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def extract_pdf(path: Path, ocr_dpi: int = 200) -> ExtractedDocument:
    pages_out: list[PageText] = []
    table_chunks: list[str] = []

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = (page.extract_text() or "").strip()
            ocr_used = False
            if len(text) < 50:
                try:
                    pil_pages = convert_from_path(
                        path, first_page=i, last_page=i, dpi=ocr_dpi
                    )
                    if pil_pages:
                        text = _ocr_page_image(pil_pages[0]).strip()
                        ocr_used = True
                except Exception:
                    pass
            pages_out.append(PageText(page_number=i, text=text, ocr_used=ocr_used))

            try:
                tables = page.extract_tables() or []
                for ti, table in enumerate(tables):
                    if not table:
                        continue
                    rows = [" | ".join(str(c or "") for c in row) for row in table]
                    table_chunks.append(f"Page {i} table {ti + 1}:\n" + "\n".join(rows))
            except Exception:
                pass

    combined = "\n\n--- Page break ---\n\n".join(
        f"[Page {p.page_number}]{' [OCR]' if p.ocr_used else ''}\n{p.text}"
        for p in pages_out
    )
    tables_summary = "\n\n".join(table_chunks) if table_chunks else ""
    return ExtractedDocument(
        pages=pages_out, combined_text=combined, tables_summary=tables_summary
    )
