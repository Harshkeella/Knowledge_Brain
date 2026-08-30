from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.models.schemas import DeleteResult, DocumentOut
from app.services import manifest
from app.services.ingestion import delete_document
from app.services.spreadsheet_query import drop_computed_column
from app.services.lightrag_engine import get_rag

# Authenticated, at the router: a route added here cannot forget it, and the
# dependency binds the identity that every store below scopes itself on.
router = APIRouter(
    prefix="/api/v1/knowledge-base",
    tags=["knowledge-base"],
    dependencies=[Depends(get_current_user)],
)


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


@router.delete("/spreadsheet/{table}/columns/{column}")
async def undo_computed_column(table: str, column: str):
    """Undo for a column added through chat. Original spreadsheet columns are
    not removable here -- only ones this feature computed."""
    if not drop_computed_column(table, column):
        raise HTTPException(
            status_code=404,
            detail=f"No computed column {column!r} on {table!r} to undo.",
        )
    return {"table": table, "column": column, "dropped": True}


@router.delete("/{doc_id}", response_model=DeleteResult)
async def delete_from_knowledge_base(doc_id: str):
    deleted = await delete_document(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")
    return DeleteResult(doc_id=doc_id, deleted=True)
