import fitz  # pymupdf


def extract_pdf_text(data: bytes) -> str:
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        return "\n\n".join(page.get_text() for page in doc).strip()
    finally:
        doc.close()
