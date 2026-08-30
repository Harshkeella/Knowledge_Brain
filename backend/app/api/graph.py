"""Three ways to read the same graph.

`GET /graph`          the whole thing (or one label's neighbourhood), capped.
`GET /graph/sources`  every Source node and nothing else -- the landing view.
`GET /graph/expand`   one node's immediate neighbours -- the hop.

The hop endpoints exist because the capped view can never show a deep tree:
`/graph` runs a degree-prioritised BFS bounded by `max_depth`/`max_nodes`, so a
folder eight levels down is not missing from the graph, it is past the horizon
of the only query that was ever asked. Neo4j Browser solved this years ago by
never loading the whole graph at all -- start from a few nodes, double-click to
pull in one more hop -- and that is what these two endpoints serve.

No `elementId()` here, and no Cypher: the store is LightRAG's NetworkX graph
and a node's identity IS its entity id, a string. That string is what the
frontend already keys on, so a hop needs no new identity concept.
"""

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import get_current_user
from app.models.schemas import GraphEdgeOut, GraphNodeOut, GraphOut
from app.services import graph_schema as gs
from app.services.lightrag_engine import get_rag

# Authenticated, at the router: a route added here cannot forget it, and the
# dependency binds the identity that every store below scopes itself on.
router = APIRouter(
    prefix="/api/v1/graph",
    tags=["graph"],
    dependencies=[Depends(get_current_user)],
)


def _node_out(node_id: str, properties: dict, degree: int) -> GraphNodeOut:
    return GraphNodeOut(
        id=node_id,
        entity_type=properties.get("entity_type"),
        description=properties.get("description"),
        file_path=properties.get("file_path"),
        degree=degree,
        source_type=properties.get("source_type"),
        status=properties.get("status"),
        qualified_name=properties.get("qualified_name"),
        signature=properties.get("signature"),
        calls_in_count=properties.get("calls_in_count"),
        calls_out_count=properties.get("calls_out_count"),
    )


def _edge_out(source: str, target: str, properties: dict) -> GraphEdgeOut:
    # Directed edges carry their own orientation (see graph_schema.upsert_edge);
    # the store's own tuple order is meaningless for them. Edges written before
    # this, and LightRAG's own undirected RELATED_TO edges, fall back to it.
    keywords = properties.get("keywords")
    return GraphEdgeOut(
        id=_edge_id(source, target),
        source=properties.get("rel_from") or source,
        target=properties.get("rel_to") or target,
        keywords=keywords,
        edge_category=gs.edge_category(keywords),
        description=properties.get("description"),
        weight=properties.get("weight"),
        file_path=properties.get("file_path"),
    )


def _edge_id(source: str, target: str) -> str:
    """The id LightRAG's own subgraph query builds: the pair, lexically sorted.

    Matching it exactly is what lets the frontend dedupe an edge that arrives
    once from `/graph` and again from an expansion.
    """
    a, b = (source, target) if str(source) <= str(target) else (target, source)
    return f"{a}-{b}"


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
        _node_out(node.id, node.properties, degrees[node.id])
        for node in kg.nodes
        if degrees.get(node.id)
    ]
    edges = [
        _edge_out(edge.source, edge.target, edge.properties) for edge in kg.edges
    ]

    return GraphOut(nodes=nodes, edges=edges, is_truncated=kg.is_truncated)


@router.get("/sources", response_model=GraphOut)
async def get_sources():
    """Every Source node, no edges, nothing else.

    Exactly `count(Source)` nodes: one per ingestion event, which is the
    landing state the hop view expands out of.
    """
    graph = (await get_rag()).chunk_entity_relation_graph
    nodes = [
        _node_out(node["id"], node, await graph.node_degree(node["id"]))
        for node in await graph.get_all_nodes()
        if node.get("entity_type") == gs.SOURCE
    ]
    return GraphOut(nodes=nodes, edges=[], is_truncated=False)


@router.get("/expand", response_model=GraphOut)
async def expand_node(node_id: str = Query(..., description="Node to expand")):
    """One hop out from `node_id`: its immediate neighbours and the edges to
    them, in both directions.

    Both directions falls out for free -- the store is an undirected NetworkX
    graph, so every incident edge is returned and each one's own `rel_from`/
    `rel_to` says which way it actually points. That is what makes expanding a
    Function surface both what it calls and what calls it, off the same query
    the folder layers use.
    """
    graph = (await get_rag()).chunk_entity_relation_graph
    if not await graph.has_node(node_id):
        raise HTTPException(status_code=404, detail=f"No such node: {node_id}")

    nodes: list[GraphNodeOut] = []
    edges: list[GraphEdgeOut] = []
    for source, target in await graph.get_node_edges(node_id) or []:
        other = target if source == node_id else source
        properties = await graph.get_node(other)
        if properties is None:
            continue  # edge to a node that was deleted out from under it
        nodes.append(_node_out(other, properties, await graph.node_degree(other)))
        edges.append(_edge_out(source, target, await graph.get_edge(source, target) or {}))

    return GraphOut(nodes=nodes, edges=edges, is_truncated=False)
