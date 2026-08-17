from collections import Counter

from fastapi import APIRouter, Query

from app.models.schemas import GraphEdgeOut, GraphNodeOut, GraphOut
from app.services.lightrag_engine import get_rag

router = APIRouter(prefix="/api/v1/graph", tags=["graph"])


@router.get("", response_model=GraphOut)
async def get_graph(
    label: str = Query("*", description="Node label to center on, or '*' for the whole graph"),
    max_depth: int = Query(3, ge=1, le=10),
    max_nodes: int = Query(300, ge=1, le=1000),
):
    rag = await get_rag()
    kg = await rag.get_knowledge_graph(label, max_depth=max_depth, max_nodes=max_nodes)

    degrees: Counter[str] = Counter()
    for edge in kg.edges:
        degrees[edge.source] += 1
        degrees[edge.target] += 1

    # LightRAG picks the top-`max_nodes` nodes by degree in the FULL graph and
    # returns the induced subgraph -- edges to cut nodes go with them, so a
    # node whose neighbours all fell outside the cut arrives with no edges at
    # all and renders as a dot drifting off the side of the force layout.
    # Those are the "disconnected nodes"; dropping them is the whole fix.
    nodes = [
        GraphNodeOut(
            id=node.id,
            entity_type=node.properties.get("entity_type"),
            description=node.properties.get("description"),
            file_path=node.properties.get("file_path"),
            degree=degrees[node.id],
        )
        for node in kg.nodes
        if degrees.get(node.id)
    ]
    edges = [
        GraphEdgeOut(
            id=edge.id,
            source=edge.source,
            target=edge.target,
            keywords=edge.properties.get("keywords"),
            description=edge.properties.get("description"),
            weight=edge.properties.get("weight"),
            file_path=edge.properties.get("file_path"),
        )
        for edge in kg.edges
    ]

    return GraphOut(nodes=nodes, edges=edges, is_truncated=kg.is_truncated)
