"""A directory tree, mirrored into the graph.

    (:Source {source_type:"folder"}) -[:HAS_ROOT]->     (:Folder)
    (:Folder) -[:CONTAINS_FOLDER]->                     (:Folder)
    (:Folder) -[:CONTAINS_FILE]->  (:File | :CodeFile | :Image | :Video)

Nothing here is extracted or inferred -- the tree in the graph is the tree on
disk. Document leaves (pdf/md/txt/xlsx) are additionally routed through the
ordinary ingestors, so their *contents* land in the knowledge base by exactly
the path a manual upload would take; a folder that only tracked file names
would be a file browser, not a knowledge base.

Node ids are `<source name>/<path relative to the root>`, so re-ingesting the
same folder rewrites the same nodes instead of duplicating the tree, and two
different folders never collide unless they were given the same name.

Code symbols (classes, functions, calls) hang off the CodeFile leaves; see
code_intel.py.
"""

import asyncio
import fnmatch
import hashlib
import logging
import mimetypes
import os
import uuid

from app.core.config import get_settings
from app.services import code_intel
from app.services import graph_schema as gs
from app.services import manifest, source_graph
from app.services.ingestion import IngestionError, ingest_file_bytes
from app.services.lightrag_engine import get_rag
from app.services.parsers.spreadsheet import SPREADSHEET_EXTENSIONS

logger = logging.getLogger("app.folder_ingest")
_settings = get_settings()

IGNORE_FILE = ".kbignore"

# Source.status lifecycle. Only folder ingestion has a phase long enough for
# this to mean anything: every other ingestor finishes inside its own request.
PROCESSING, COMPLETED, FAILED = "processing", "completed", "failed"

# Detached document passes, held so the event loop cannot garbage-collect a
# task mid-flight (asyncio keeps only weak references to running tasks).
_background: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


async def wait_for_documents() -> None:
    """Block until every deferred document pass has finished. For scripts and
    tests, which have no browser to poll the Source node's status."""
    while _background:
        await asyncio.gather(*list(_background), return_exceptions=True)

# Build output, dependency trees and tool caches: never the content, always the
# bulk. Extend per-folder with a .kbignore rather than editing this.
DEFAULT_IGNORES = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode", ".serena",
        "node_modules", "bower_components", "vendor",
        "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        "venv", ".venv", "env", ".env",
        "dist", "build", "out", "target", ".next", ".nuxt", ".turbo",
        "coverage", ".coverage", ".cache", ".parcel-cache",
        "__MACOSX", ".DS_Store", ".gradle", "Pods",
    }
)

# Extension -> language. The language string is what the code parser registry
# dispatches on; a file whose extension is here is a CodeFile even if no parser
# for that language exists yet.
CODE_LANGUAGES = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cs": "c_sharp", ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".scala": "scala", ".sh": "bash", ".sql": "sql", ".lua": "lua",
    ".r": "r", ".pl": "perl", ".vue": "vue", ".svelte": "svelte",
}
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
     ".tif", ".tiff", ".avif"}
)
# Audio rides with video: both are timed media whose leaf carries metadata only
# until a transcription hook runs.
VIDEO_EXTENSIONS = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg",
     ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma"}
)
DOCUMENT_EXTENSIONS = frozenset({".pdf", ".md", ".markdown", ".txt"}) | set(
    SPREADSHEET_EXTENSIONS
)


def load_ignores(root: str) -> list[str]:
    """DEFAULT_IGNORES plus the root's .kbignore, one glob per line.

    Deliberately not a .gitignore implementation: no negation, no anchoring, no
    directory-only semantics. A pattern matches a name or a relative path, and
    that covers what a scan actually needs to skip.
    """
    patterns = list(DEFAULT_IGNORES)
    ignore_path = os.path.join(root, IGNORE_FILE)
    if os.path.isfile(ignore_path):
        with open(ignore_path, encoding="utf-8", errors="replace") as handle:
            patterns += [
                line.strip().rstrip("/")
                for line in handle
                if line.strip() and not line.lstrip().startswith("#")
            ]
    return patterns


def is_ignored(name: str, rel_path: str, patterns: list[str]) -> bool:
    rel = rel_path.replace(os.sep, "/")
    return any(
        fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(rel, pattern)
        for pattern in patterns
    )


def classify(ext: str) -> str:
    """Which label a leaf gets. Extension only -- mimetypes disagrees with
    itself across platforms (it reads the Windows registry here), so it fills
    in a property but never decides the label."""
    if ext in CODE_LANGUAGES:
        return gs.CODE_FILE
    if ext in IMAGE_EXTENSIONS:
        return gs.IMAGE
    if ext in VIDEO_EXTENSIONS:
        return gs.VIDEO
    return gs.FILE


def _image_size(path: str) -> tuple[int, int] | None:
    """Pillow if it is installed, nothing if it is not.

    Dimensions are a nice-to-have on an Image node, not a reason to make every
    install carry an imaging library -- Pillow lives in the optional
    requirements-codeintel.txt extra.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as img:
            return img.size
    except Exception:
        return None


def _resolve_root(path: str) -> str:
    root = os.path.abspath(os.path.expanduser(path.strip()))
    if not os.path.isdir(root):
        raise IngestionError(f"Not a directory: {path}")

    # A path parameter that reaches the filesystem is a trust boundary even on
    # localhost. Unset means the operator accepted that; set means enforce it.
    confine = _settings.folder_ingest_root
    if confine:
        allowed = os.path.abspath(os.path.expanduser(confine))
        try:
            inside = os.path.commonpath([allowed, root]) == allowed
        except ValueError:
            inside = False  # different drives on Windows
        if not inside:
            raise IngestionError(
                f"Folder ingestion is confined to {allowed}; {root} is outside it."
            )
    return root


def scan(root: str, name: str) -> dict:
    """Walk the tree and return the nodes to write.

    No writes and no rag -- separated from the graph pass so the walk, the
    ignore rules and the classification are testable on a tmp_path alone.
    """
    patterns = load_ignores(root)
    folders: list[dict] = []
    files: list[dict] = []
    max_bytes = int(_settings.folder_max_file_mb * 1024 * 1024)

    max_depth = _settings.folder_max_depth
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        depth = rel_dir.count("/") + 1 if rel_dir else 0

        # Prune in place so os.walk never descends into an ignored tree.
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not is_ignored(d, f"{rel_dir}/{d}".lstrip("/"), patterns)
        )
        if depth >= max_depth:
            # Only ever reachable when an operator sets FOLDER_MAX_DEPTH; the
            # default sentinel is far past any real tree.
            logger.warning("Depth limit %d reached at %s", max_depth, rel_dir)
            dirnames[:] = []

        node_id = f"{name}/{rel_dir}" if rel_dir else name
        folders.append(
            {
                "id": node_id,
                "name": os.path.basename(dirpath) if rel_dir else name,
                "rel_path": rel_dir,
                "depth": depth,
                "parent": _parent_of(name, rel_dir),
                "children": list(dirnames),
            }
        )

        for filename in sorted(filenames):
            rel = f"{rel_dir}/{filename}".lstrip("/")
            if is_ignored(filename, rel, patterns) or filename == IGNORE_FILE:
                continue
            full = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            ext = os.path.splitext(filename)[1].lower()
            files.append(
                {
                    "id": f"{name}/{rel}",
                    "name": filename,
                    "rel_path": rel,
                    "full_path": full,
                    "parent": node_id,
                    "ext": ext,
                    "label": classify(ext),
                    "language": CODE_LANGUAGES.get(ext),
                    "mime_type": mimetypes.guess_type(filename)[0],
                    "size_bytes": size,
                    "too_big": size > max_bytes,
                }
            )

    _log_levels(name, folders, files)
    return {"folders": folders, "files": files}


def _log_levels(name: str, folders: list[dict], files: list[dict]) -> None:
    """One line per level, so a future depth regression is readable straight
    off the log without re-deriving ground truth from the filesystem."""
    per_level: dict[int, list[int]] = {}
    by_id = {entry["id"]: entry["depth"] for entry in folders}
    for entry in folders:
        per_level.setdefault(entry["depth"], [0, 0])[0] += 1
    for entry in files:
        depth = by_id.get(entry["parent"], 0)
        per_level.setdefault(depth, [0, 0])[1] += 1
    for depth in sorted(per_level):
        folder_count, file_count = per_level[depth]
        logger.info(
            "scan %s: depth=%d folders=%d files=%d", name, depth, folder_count, file_count
        )


def _parent_of(name: str, rel_dir: str) -> str | None:
    if not rel_dir:
        return None
    if "/" not in rel_dir:
        return name
    return f"{name}/{rel_dir.rsplit('/', 1)[0]}"


def _file_description(entry: dict, name: str) -> str:
    kind = {
        gs.CODE_FILE: f"{entry['language']} source file",
        gs.IMAGE: "Image",
        gs.VIDEO: "Media file",
    }.get(entry["label"], "File")
    mime = f", {entry['mime_type']}" if entry["mime_type"] else ""
    return (
        f"{kind} {entry['rel_path']} in the folder {name} "
        f"({entry['size_bytes']} bytes{mime})."
    )


async def ingest_folder(
    path: str, name: str | None = None, index_documents: bool = True
) -> dict:
    """Mirror a folder into the graph. Idempotent on the same path + name."""
    root = _resolve_root(path)
    name = (name or os.path.basename(root) or "folder").strip()
    tree = scan(root, name)
    folders, files = tree["folders"], tree["files"]
    if not files and len(folders) <= 1:
        raise IngestionError(f"Nothing to ingest under {root} (everything ignored?).")

    total_bytes = sum(entry["size_bytes"] for entry in files)
    doc_id = f"doc-{uuid.uuid4().hex}"
    # Not a content hash -- the tree's shape. A folder has no single body to
    # hash, and hashing every file just to dedup a re-scan is work nobody
    # asked for.
    listing = "\n".join(f"{e['rel_path']}:{e['size_bytes']}" for e in files)
    content_hash = f"folder-sha256:{hashlib.sha256(listing.encode()).hexdigest()}"

    existing = await manifest.find_by_name(name)
    if existing is not None:
        # Re-ingest: reuse the inventory row's doc_id and rewrite the tree over
        # itself, so the graph tracks the folder as it is now.
        doc_id = existing["doc_id"]
        await manifest.delete_document(doc_id)

    record = await manifest.insert_document(
        doc_id=doc_id,
        file_name=name,
        source_type="folder",
        content_hash=content_hash,
        chunk_count=len(files),
        size_bytes=total_bytes,
    )

    rag = await get_rag()
    counts = {
        "code_files": sum(1 for e in files if e["label"] == gs.CODE_FILE),
        "images": sum(1 for e in files if e["label"] == gs.IMAGE),
        "videos": sum(1 for e in files if e["label"] == gs.VIDEO),
        "folder_count": len(folders),
        "file_count": len(files),
        # Same three numbers under the names the depth audit asks for, so
        # "did this tree land whole?" is a property read, not a graph query.
        "total_folders": len(folders),
        "total_files": len(files),
        "max_depth_reached": max((e["depth"] for e in folders), default=0),
        "origin_path": root,
        "status": PROCESSING,
    }
    await source_graph.create(rag, record, **counts)

    for entry in folders:
        await gs.upsert_node(
            rag,
            entry["id"],
            gs.FOLDER,
            description=(
                f"Folder {entry['rel_path'] or name} of {name}, containing "
                f"{len(entry['children'])} subfolder(s)."
            ),
            file_path=name,
            source_id=doc_id,
            path=entry["rel_path"],
            depth=entry["depth"],
        )
        if entry["parent"]:
            await gs.upsert_edge(
                rag,
                entry["parent"],
                entry["id"],
                gs.CONTAINS_FOLDER,
                description=f"{entry['parent']} contains {entry['name']}.",
                file_path=name,
                source_id=doc_id,
            )
    # One edge from the supernode to the tree's root; everything else is
    # reachable through it.
    await source_graph.attach(rag, record, [name])

    errors: list[dict] = []
    pending: list[dict] = []
    sources: dict[str, str] = {}
    for entry in files:
        properties = {
            "path": entry["rel_path"],
            "ext": entry["ext"],
            "size_bytes": entry["size_bytes"],
        }
        if entry["mime_type"]:
            properties["mime_type"] = entry["mime_type"]
        if entry["language"]:
            properties["language"] = entry["language"]
        if entry["label"] == gs.IMAGE:
            size = _image_size(entry["full_path"])
            if size:
                properties["width"], properties["height"] = size
        if entry["label"] == gs.CODE_FILE and not entry["too_big"]:
            # Read once: `loc` is needed on the node now, the text is needed by
            # the symbol pass after every file node exists.
            try:
                with open(entry["full_path"], encoding="utf-8", errors="replace") as f:
                    text = f.read()
                sources[entry["id"]] = text
                properties["loc"] = text.count("\n") + 1
            except OSError as e:
                errors.append({"file_name": entry["rel_path"], "error": str(e)})
        # ponytail: no per-file checksum yet. Node ids are paths so a re-scan
        # already rewrites in place; add sha256 here when skipping unchanged
        # files is worth the read.

        if index_documents and entry["ext"] in DOCUMENT_EXTENSIONS:
            if entry["too_big"]:
                errors.append({"file_name": entry["rel_path"], "error": "too large"})
            else:
                # Deferred, deliberately. Routing one document through the
                # LLM pipeline takes minutes; doing it HERE put those minutes
                # inside the tree-write loop and inside the HTTP request, so a
                # client timeout cancelled the walk (CancelledError is a
                # BaseException -- no `except Exception` below caught it) and
                # the closing flush never ran. Everything past the first
                # document leaf was lost. The tree is written and flushed
                # first now; documents follow, detached.
                pending.append(entry)

        await gs.upsert_node(
            rag,
            entry["id"],
            entry["label"],
            description=_file_description(entry, name),
            file_path=name,
            source_id=doc_id,
            **properties,
        )
        await gs.upsert_edge(
            rag,
            entry["parent"],
            entry["id"],
            gs.CONTAINS_FILE,
            description=f"{entry['parent']} contains {entry['name']}.",
            file_path=name,
            source_id=doc_id,
        )

    # Symbols last: a call in the first file can land in the last, so the whole
    # tree has to be parsed before any CALLS edge can be resolved.
    by_id = {entry["id"]: entry for entry in files}
    parsed = {
        file_node: code_intel.extract(text, by_id[file_node]["language"])
        for file_node, text in sources.items()
    }
    parsed = {k: v for k, v in parsed.items() if v.symbols or v.imports}
    code_counts = (
        await code_intel.project(
            rag, parsed, {k: by_id[k] for k in parsed}, name, doc_id
        )
        if parsed
        else {"classes": 0, "functions": 0, "methods": 0, "calls": 0, "unresolved": 0}
    )

    # Everything above went straight into the graph, outside LightRAG's own
    # pipeline. Without this the whole tree and every symbol lives only in
    # memory and dies with the process. This is now the DURABILITY POINT: the
    # complete tree and every symbol are on disk before any slow work starts.
    status = PROCESSING if pending else COMPLETED
    if not pending:
        # Before the flush, not after: a status written past the commit point
        # lives in memory only and the node on disk stays `processing` forever.
        await source_graph.set_status(rag, name, status=status, documents_indexed=0)
    await gs.flush(rag)
    logger.info(
        "Ingested %s: %d folder(s) (max depth %d), %d file(s), %s symbols, "
        "%d document(s) queued, %d error(s)",
        root, len(folders), counts["max_depth_reached"], len(files), code_counts,
        len(pending), len(errors),
    )
    if pending:
        _spawn(_index_documents(rag, name, doc_id, pending, list(errors)))

    return {
        **record,
        **code_counts,
        "name": name,
        "path": root,
        "folders": len(folders),
        "files": len(files),
        "code_files": counts["code_files"],
        "images": counts["images"],
        "videos": counts["videos"],
        # Documents are indexed after this returns, so this is what has landed
        # *so far*; `status` says whether more is coming and the Source node
        # carries the final number. `documents_pending` is what is queued.
        "documents_indexed": 0,
        "documents_pending": len(pending),
        "status": status,
        "max_depth_reached": counts["max_depth_reached"],
        "total_folders": len(folders),
        "total_files": len(files),
        "errors": errors,
    }


async def _index_documents(
    rag, name: str, doc_id: str, entries: list[dict], errors: list[dict]
) -> None:
    """Second pass: route document leaves through the ordinary ingestors.

    Detached from the request on purpose -- this is the part that takes
    minutes, and nothing it does is load-bearing for the tree, which is
    already on disk. A failure here downgrades the Source node's status; it
    can never cost you the folder structure again.
    """
    indexed = 0
    status = COMPLETED
    try:
        for entry in entries:
            try:
                with open(entry["full_path"], "rb") as handle:
                    doc = await ingest_file_bytes(handle.read(), entry["name"])
            except Exception as e:
                # One unreadable file must not abandon the rest of the tree;
                # the leaf already exists, it just isn't indexed.
                logger.warning("Could not index %s", entry["full_path"], exc_info=True)
                errors.append({"file_name": entry["rel_path"], "error": str(e)})
                continue
            await gs.update_node(rag, entry["id"], doc_id=doc["doc_id"])
            indexed += 1
    except BaseException:
        # Including CancelledError: record the truth rather than leaving the
        # node stuck on `processing` forever.
        status = FAILED
        raise
    finally:
        await source_graph.set_status(
            rag,
            name,
            status=status,
            documents_indexed=indexed,
            document_errors=len(errors),
        )
        await gs.flush(rag)
        logger.info(
            "Indexed %d/%d document(s) from %s (%s)",
            indexed, len(entries), name, status,
        )


async def remove(rag, name: str) -> int:
    """Drop every node of a folder ingest: the supernode, the tree, its leaves,
    and any code symbols hanging off them.

    The supernode is removed here rather than by the caller so there is no way
    to half-delete a folder -- a dangling `source:` node pointing at a tree
    that no longer exists is exactly the state a second call site would leave
    behind. Node ids are all prefixed by the source name, which is what makes
    this one pass instead of a traversal.
    """
    prefix = f"{name}/"
    doomed = [source_graph.source_node(name)] + [
        node["id"]
        for node in await rag.chunk_entity_relation_graph.get_all_nodes()
        if node["id"] == name or str(node["id"]).startswith(prefix)
    ]
    await gs.remove_nodes(rag, doomed)
    await gs.flush(rag)
    return len(doomed)


if __name__ == "__main__":
    assert classify(".py") == gs.CODE_FILE
    assert classify(".png") == gs.IMAGE
    assert classify(".mp3") == gs.VIDEO, "audio rides with video"
    assert classify(".pdf") == gs.FILE, "documents are plain File leaves"

    patterns = ["node_modules", "*.log", "docs/private"]
    assert is_ignored("node_modules", "a/node_modules", patterns)
    assert is_ignored("run.log", "a/run.log", patterns)
    assert is_ignored("private", "docs/private", patterns)
    assert not is_ignored("main.py", "src/main.py", patterns)

    assert _parent_of("proj", "") is None
    assert _parent_of("proj", "src") == "proj"
    assert _parent_of("proj", "src/api") == "proj/src"
    print("ok")
