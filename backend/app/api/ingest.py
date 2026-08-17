import logging
import os

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.schemas import IngestResult, TextIngestRequest, UrlIngestRequest
from app.services.ingestion import IngestionError, ingest_text
from app.services.parsers.pdf import extract_pdf_text
from app.services.parsers.text import extract_plain_text
from app.services.parsers.url import extract_article, is_youtube_url
from app.services.parsers.youtube import extract_youtube_transcript

logger = logging.getLogger("app.api.ingest")

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])

_TEXT_EXTENSIONS = {".md", ".txt", ".markdown"}


@router.post("/file")
async def ingest_files(files: list[UploadFile] = File(...)):
    results: list[IngestResult] = []
    errors: list[dict] = []

    for upload in files:
        file_name = upload.filename or "untitled"
        ext = os.path.splitext(file_name)[1].lower()
        try:
            data = await upload.read()
            if ext == ".pdf":
                text = extract_pdf_text(data)
                source_type = "pdf"
            elif ext in _TEXT_EXTENSIONS:
                text = extract_plain_text(data)
                source_type = "markdown" if ext in (".md", ".markdown") else "text"
            else:
                raise IngestionError(f"Unsupported file type: {ext or '(none)'}")

            record = await ingest_text(text, file_name, source_type)
            results.append(IngestResult(**record))
        except IngestionError as e:
            errors.append({"file_name": file_name, "error": str(e)})
        except Exception as e:
            logger.exception("Failed to ingest file %s", file_name)
            errors.append({"file_name": file_name, "error": str(e)})

    return {"results": results, "errors": errors}


@router.post("/url")
async def ingest_url(payload: UrlIngestRequest):
    url = payload.url.strip()
    try:
        if is_youtube_url(url):
            text, video_id = extract_youtube_transcript(url)
            file_name = f"YouTube: {video_id}"
            source_type = "youtube"
        else:
            text, title, source_type = await extract_article(url)
            file_name = title or url

        record = await ingest_text(text, file_name, source_type)
        return IngestResult(**record)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to ingest URL %s", url)
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/text")
async def ingest_pasted_text(payload: TextIngestRequest):
    title = (payload.title or "").strip()
    file_name = title or (payload.text.strip()[:60] or "Pasted text")
    try:
        record = await ingest_text(payload.text, file_name, "paste")
        return IngestResult(**record)
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
