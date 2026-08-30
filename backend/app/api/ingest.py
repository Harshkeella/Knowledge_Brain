import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.models.schemas import (
    FolderIngestRequest,
    FolderIngestResult,
    IngestResult,
    TextIngestRequest,
    UrlIngestRequest,
)
from app.services import folder_ingest
from app.services.ingestion import (
    IngestionError,
    QuotaExceeded,
    ingest_file_bytes,
    ingest_text,
)
from app.services.parsers.url import extract_article, is_youtube_url
from app.services.parsers.youtube import extract_youtube_transcript

logger = logging.getLogger("app.api.ingest")

# Every route here is authenticated: the dependency both rejects an
# unauthenticated request and binds the identity the storage layer scopes on,
# so an endpoint that forgot it would not merely be public -- it would have no
# storage to reach. Declared on the router so a new route cannot omit it.
router = APIRouter(
    prefix="/api/v1/ingest",
    tags=["ingest"],
    dependencies=[Depends(get_current_user)],
)

_settings = get_settings()

_TEXT_SOURCE_TYPES = {"paste", "article_clipper"}


@router.post("/file")
async def ingest_files(files: list[UploadFile] = File(...)):
    if len(files) > _settings.max_files_per_batch:
        raise HTTPException(
            status_code=413,
            detail=f"Too many files in one upload (max {_settings.max_files_per_batch}).",
        )

    results: list[IngestResult] = []
    errors: list[dict] = []
    batch_bytes = 0

    for upload in files:
        file_name = upload.filename or "untitled"
        try:
            # Read the file once and measure it before anything parses it, so
            # an oversized upload is rejected on size rather than after a PDF
            # parser has already been handed it.
            data = await upload.read()
            if len(data) > _settings.max_file_size_bytes:
                raise IngestionError(
                    f"{file_name} is larger than the "
                    f"{_settings.max_file_size_bytes // 1024**2} MB per-file limit."
                )
            batch_bytes += len(data)
            if batch_bytes > _settings.max_batch_size_bytes:
                raise IngestionError(
                    "This upload exceeds the "
                    f"{_settings.max_batch_size_bytes // 1024**2} MB batch limit."
                )
            record = await ingest_file_bytes(data, file_name)
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
    except QuotaExceeded as e:
        raise HTTPException(status_code=413, detail=str(e))
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("Failed to ingest URL %s", url)
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/folder", response_model=FolderIngestResult)
async def ingest_folder(payload: FolderIngestRequest):
    """Ingest a directory tree that is already on the server's disk.

    Path-based, which is only ever safe when the caller IS the machine: the
    path names the server's own filesystem, so on a shared deployment it is an
    arbitrary-file-read endpoint with a knowledge base attached. It is
    therefore refused outright whenever authentication is on, and confined to
    FOLDER_INGEST_ROOT when it is not.

    The browser alternative (`webkitdirectory`, which re-posts every byte
    through multipart) is what a hosted deployment should offer instead; it
    goes through /ingest/file and is already quota-checked.
    """
    if not _settings.auth_disabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "Server-path folder ingestion is disabled on a hosted "
                "deployment. Upload the folder's files instead."
            ),
        )
    try:
        return FolderIngestResult(
            **await folder_ingest.ingest_folder(
                payload.path,
                name=payload.name,
                index_documents=payload.index_documents,
            )
        )
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/text")
async def ingest_pasted_text(payload: TextIngestRequest):
    title = (payload.title or "").strip()
    file_name = title or (payload.text.strip()[:60] or "Pasted text")
    source_type = payload.source_type if payload.source_type in _TEXT_SOURCE_TYPES else "paste"
    try:
        record = await ingest_text(payload.text, file_name, source_type)
        return IngestResult(**record)
    except QuotaExceeded as e:
        raise HTTPException(status_code=413, detail=str(e))
    except IngestionError as e:
        raise HTTPException(status_code=422, detail=str(e))
