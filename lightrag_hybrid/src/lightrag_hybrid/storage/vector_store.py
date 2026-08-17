"""Storage backends for vectors and graph data."""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
import numpy as np
from chromadb import PersistentClient
from chromadb.api.models.Collection import Collection

from lightrag_hybrid.core.config import get_settings
from lightrag_hybrid.core.models import Chunk, Entity, Relationship
from lightrag_hybrid.utils.text_utils import setup_logging

logger = setup_logging()


class VectorStore:
    """ChromaDB-based vector store for chunk embeddings."""

    def __init__(self, collection_name: str | None = None):
        self.settings = get_settings()
        self.client = PersistentClient(path=str(self.settings.vector_db_full_path))
        self.collection: Collection = self.client.get_or_create_collection(
            name=collection_name or self.settings.vector_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._hash_cache: Set[str] = set()
        self._load_hash_cache()

    def _load_hash_cache(self) -> None:
        """Load existing content hashes for deduplication."""
        try:
            result = self.collection.get(include=["metadatas"])
            if result and "metadatas" in result:
                for meta in result["metadatas"]:
                    if meta and "content_hash" in meta:
                        self._hash_cache.add(meta["content_hash"])
            logger.info(f"Loaded {len(self._hash_cache)} existing chunk hashes")
        except Exception as e:
            logger.warning(f"Could not load hash cache: {e}")

    def has_hash(self, content_hash: str) -> bool:
        return content_hash in self._hash_cache

    def add_chunks(self, chunks: List[Chunk]) -> List[Chunk]:
        """Add chunks to vector store, skipping duplicates."""
        new_chunks = [c for c in chunks if not self.has_hash(c.content_hash)]
        if not new_chunks:
            logger.info("All chunks already exist in vector store")
            return []

        ids = [c.id for c in new_chunks]
        documents = [c.content for c in new_chunks]
        embeddings = [c.embedding for c in new_chunks if c.embedding is not None]
        metadatas = [
            {
                "filename": c.source_doc.filename,
                "file_path": c.source_doc.file_path,
                "chunk_index": c.chunk_index,
                "content_hash": c.content_hash,
                "token_count": c.token_count,
            }
            for c in new_chunks
        ]

        if embeddings and len(embeddings) == len(new_chunks):
            self.collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )
        else:
            # Let Chroma compute embeddings
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas,
            )

        for c in new_chunks:
            self._hash_cache.add(c.content_hash)

        logger.info(f"Added {len(new_chunks)} new chunks to vector store")
        return new_chunks

    def search(
        self, query_embedding: List[float], top_k: int | None = None
    ) -> List[Tuple[Chunk, float]]:
        """Search vector store by embedding. Returns (chunk, score)."""
        k = top_k or self.settings.top_k_vector
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        output: List[Tuple[Chunk, float]] = []
        if not results or not results["ids"]:
            return output

        for idx, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][idx] if results["metadatas"] else {}
            doc = results["documents"][0][idx] if results["documents"] else ""
            distance = results["distances"][0][idx] if results["distances"] else 1.0
            score = 1.0 - distance  # convert distance to similarity

            from lightrag_hybrid.core.models import DocumentSource

            chunk = Chunk(
                id=doc_id,
                content=doc,
                source_doc=DocumentSource(
                    filename=meta.get("filename", "unknown"),
                    file_path=meta.get("file_path", ""),
                    file_type="unknown",
                ),
                chunk_index=meta.get("chunk_index", 0),
                token_count=meta.get("token_count", 0),
                char_count=len(doc),
                content_hash=meta.get("content_hash", ""),
            )
            output.append((chunk, score))

        return output

    def delete_by_filename(self, filename: str) -> int:
        """Delete all chunks from a specific file."""
        results = self.collection.get(
            where={"filename": filename},
            include=[],
        )
        if results and results["ids"]:
            self.collection.delete(ids=results["ids"])
            return len(results["ids"])
        return 0

    def get_stats(self) -> Dict[str, int]:
        return {"total_chunks": self.collection.count()}


class GraphStore:
    """NetworkX-based graph store with incremental merge support."""

    def __init__(self, db_path: str | None = None):
        self.settings = get_settings()
        self.db_path = Path(db_path or self.settings.graph_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.graph = nx.DiGraph()
        self.entity_store: Dict[str, Entity] = {}
        self.relationship_store: Dict[str, Relationship] = {}
        self._load()

    def _get_entity_key(self, name: str, entity_type: str) -> str:
        """Generate unique key for entity deduplication."""
        return f"{entity_type.lower()}:{name.lower()}"

    def _get_relationship_key(self, rel: Relationship) -> str:
        return f"{rel.source.lower()}|{rel.relation_type.lower()}|{rel.target.lower()}"

    def _load(self) -> None:
        """Load graph from disk."""
        graph_file = self.db_path / "graph.pkl"
        entities_file = self.db_path / "entities.pkl"
        rels_file = self.db_path / "relationships.pkl"

        if graph_file.exists():
            try:
                with open(graph_file, "rb") as f:
                    self.graph = pickle.load(f)
                logger.info(f"Loaded graph with {self.graph.number_of_nodes()} nodes, {self.graph.number_of_edges()} edges")
            except Exception as e:
                logger.warning(f"Failed to load graph: {e}")

        if entities_file.exists():
            try:
                with open(entities_file, "rb") as f:
                    self.entity_store = pickle.load(f)
            except Exception as e:
                logger.warning(f"Failed to load entities: {e}")

        if rels_file.exists():
            try:
                with open(rels_file, "rb") as f:
                    self.relationship_store = pickle.load(f)
            except Exception as e:
                logger.warning(f"Failed to load relationships: {e}")

    def save(self) -> None:
        """Persist graph to disk."""
        self.db_path.mkdir(parents=True, exist_ok=True)
        with open(self.db_path / "graph.pkl", "wb") as f:
            pickle.dump(self.graph, f)
        with open(self.db_path / "entities.pkl", "wb") as f:
            pickle.dump(self.entity_store, f)
        with open(self.db_path / "relationships.pkl", "wb") as f:
            pickle.dump(self.relationship_store, f)
        logger.info("Graph saved to disk")

    def merge_entities(self, entities: List[Entity]) -> List[Entity]:
        """Incrementally merge entities into the graph."""
        merged = []
        for entity in entities:
            key = self._get_entity_key(entity.name, entity.entity_type)
            if key in self.entity_store:
                existing = self.entity_store[key]
                # Merge attributes
                existing.attributes.update(entity.attributes)
                # Merge source references
                existing.source_chunks = list(set(existing.source_chunks + entity.source_chunks))
                existing.source_docs = list(set(existing.source_docs + entity.source_docs))
                existing.last_seen = entity.last_seen
                # Update graph node
                if self.graph.has_node(key):
                    self.graph.nodes[key]["entity"] = existing
            else:
                self.entity_store[key] = entity
                self.graph.add_node(key, entity=entity, name=entity.name, type=entity.entity_type)
                merged.append(entity)

        return merged

    def merge_relationships(self, relationships: List[Relationship]) -> List[Relationship]:
        """Incrementally merge relationships into the graph."""
        merged = []
        for rel in relationships:
            key = self._get_relationship_key(rel)
            if key in self.relationship_store:
                existing = self.relationship_store[key]
                existing.weight += rel.weight
                existing.source_chunks = list(set(existing.source_chunks + rel.source_chunks))
                existing.source_docs = list(set(existing.source_docs + rel.source_docs))
                if rel.description and not existing.description:
                    existing.description = rel.description
            else:
                self.relationship_store[key] = rel
                src_key = self._get_entity_key(rel.source, "unknown")
                tgt_key = self._get_entity_key(rel.target, "unknown")
                self.graph.add_edge(
                    src_key, tgt_key,
                    relationship=rel,
                    relation_type=rel.relation_type,
                    weight=rel.weight,
                )
                merged.append(rel)

        return merged

    def get_neighbors(
        self, entity_name: str, entity_type: str = "unknown", hops: int = 1
    ) -> Tuple[List[Entity], List[Relationship]]:
        """Get n-hop neighbors of an entity."""
        key = self._get_entity_key(entity_name, entity_type)
        if key not in self.graph:
            return [], []

        entities = set()
        relationships = []

        # BFS up to hops
        visited = {key}
        frontier = {key}

        for _ in range(hops):
            new_frontier = set()
            for node in frontier:
                for neighbor in self.graph.successors(node):
                    if neighbor not in visited:
                        new_frontier.add(neighbor)
                        if neighbor in self.entity_store:
                            entities.add(self.entity_store[neighbor])
                    edge_data = self.graph.get_edge_data(node, neighbor)
                    if edge_data and "relationship" in edge_data:
                        relationships.append(edge_data["relationship"])

                if self.graph.is_directed():
                    for neighbor in self.graph.predecessors(node):
                        if neighbor not in visited:
                            new_frontier.add(neighbor)
                            if neighbor in self.entity_store:
                                entities.add(self.entity_store[neighbor])
                            edge_data = self.graph.get_edge_data(neighbor, node)
                            if edge_data and "relationship" in edge_data:
                                relationships.append(edge_data["relationship"])

            visited.update(new_frontier)
            frontier = new_frontier

        return list(entities), relationships

    def find_paths(
        self, source: str, target: str, max_length: int = 3
    ) -> List[List[Relationship]]:
        """Find paths between two entities."""
        src_key = self._get_entity_key(source, "unknown")
        tgt_key = self._get_entity_key(target, "unknown")

        if src_key not in self.graph or tgt_key not in self.graph:
            return []

        paths = []
        try:
            for path in nx.all_simple_paths(
                self.graph, src_key, tgt_key, cutoff=max_length
            ):
                rels = []
                for i in range(len(path) - 1):
                    edge_data = self.graph.get_edge_data(path[i], path[i + 1])
                    if edge_data and "relationship" in edge_data:
                        rels.append(edge_data["relationship"])
                if rels:
                    paths.append(rels)
        except nx.NetworkXNoPath:
            pass

        return paths

    def get_communities(self) -> List[List[str]]:
        """Get graph communities using Louvain algorithm."""
        if self.graph.number_of_edges() == 0:
            return []
        # Convert to undirected for community detection
        undirected = self.graph.to_undirected()
        try:
            import community as community_louvain
            partition = community_louvain.best_partition(undirected)
            communities: Dict[int, List[str]] = {}
            for node, comm_id in partition.items():
                communities.setdefault(comm_id, []).append(node)
            return list(communities.values())
        except ImportError:
            logger.warning("python-louvain not installed, using connected components")
            return [list(c) for c in nx.connected_components(undirected)]

    def get_stats(self) -> Dict[str, int]:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "entities": len(self.entity_store),
            "relationships": len(self.relationship_store),
        }
