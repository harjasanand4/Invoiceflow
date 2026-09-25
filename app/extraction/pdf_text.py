"""Turn PDF bytes into plain text."""
from __future__ import annotations

import io

import pdfplumber


def extract_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(pages).strip()


def is_pdf(data: bytes) -> bool:
    return data[:5] == b"%PDF-"
