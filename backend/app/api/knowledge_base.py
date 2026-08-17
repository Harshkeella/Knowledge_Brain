from fastapi import APIRouter, HTTPException

from app.models.schemas import DeleteResult, DocumentOut
from app.services import manifest
from app.services.ingestion import delete_document
from app.services.lightrag_engine import get_rag

router = APIRouter(prefix="/api/v1/knowledge-base", tags=["knowledge-base"])


@router.get("", response_model=list[DocumentOut])
async def list_knowledge_base():
    return await manifest.list_documents()


@router.post("/reprocess")
async def reprocess_pending():
    """Manually resume any document left pending/processing/failed by an
    interrupted run (e.g. a crash or a dev-server reload mid-ingestion).
    Not run automatically on startup — that would re-trigger costly LLM
    extraction on every `--reload` restart."""
    rag = await get_rag()
    await rag.apipeline_process_enqueue_documents()
    return {"status": "ok"}


@router.delete("/{doc_id}", response_model=DeleteResult)
async def delete_from_knowledge_base(doc_id: str):
    deleted = await delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return DeleteResult(doc_id=doc_id, deleted=True)
