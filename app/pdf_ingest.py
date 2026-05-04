"""PDF validation and safe saving."""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF


class PdfValidationError(Exception):
    pass


def validate_pdf(path: Path, max_size_mb: float) -> None:
    if not path.exists():
        raise PdfValidationError("File not found")
    size_mb = path.stat().st_size / (1024 * 1024)
    if size_mb > max_size_mb:
        raise PdfValidationError(f"File exceeds maximum size ({max_size_mb} MB)")
    try:
        doc = fitz.open(path)
        page_count = doc.page_count
        doc.close()
    except Exception as e:
        raise PdfValidationError(f"Invalid PDF: {e}") from e
    if page_count < 1:
        raise PdfValidationError("PDF has no pages")


def save_uploaded_pdf(data: bytes, dest_dir: Path, filename: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name.replace("..", "_")
    dest = dest_dir / safe_name
    dest.write_bytes(data)
    return dest
