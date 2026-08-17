def extract_plain_text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip()
