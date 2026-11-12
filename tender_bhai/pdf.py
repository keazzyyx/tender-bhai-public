"""PDF text extraction with pdfplumber (bounded pages, never raises)."""

from __future__ import annotations

import io
import logging

log = logging.getLogger(__name__)

MAX_PAGES = 12
MAX_CHARS = 30000


def pdf_to_text(data: bytes | None, max_pages: int = MAX_PAGES) -> str:
    if not data or not data.lstrip().startswith(b"%PDF"):
        return ""
    try:
        import pdfplumber

        chunks: list[str] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages[:max_pages]:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text)
                if sum(len(c) for c in chunks) > MAX_CHARS:
                    break
        return "\n".join(chunks)[:MAX_CHARS]
    except Exception as exc:  # pdfplumber raises many exception types on bad files
        log.warning("pdf extraction failed: %s", exc)
        return ""
