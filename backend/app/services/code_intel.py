"""Classes, functions, methods and who-calls-whom, hung off the CodeFile nodes.

    (:CodeFile) -[:DEFINES]->        (:Class | :Function)
    (:Class)    -[:DEFINES_METHOD]-> (:Method)
    (:Class)    -[:INHERITS]->       (:Class)
    (:Function | :Method) -[:CALLS]-> (:Function | :Method)
    (:CodeFile) -[:IMPORTS]->        (:CodeFile)

Two extraction backends behind one interface:

* **Python** uses the standard library's `ast`. No dependency, no grammar
  download, correct by construction for the language this backend is written
  in.
* **Everything else** uses tree-sitter, which needs grammars -- an OPTIONAL
  extra (`requirements-codeintel.txt`). Without it, code files are still
  ingested as CodeFile leaves and simply carry no symbols; nothing raises.

Deliberate ceilings, all of them the difference between a shippable pass and a
compiler:

* Only module-level definitions and class methods become nodes. A closure or a
  callback is not its own node -- its calls are attributed to the function that
  encloses it. Otherwise a React file yields a node per inline arrow function.
* Call resolution is by NAME, scoped file-first then project-wide, and only
  when the name is unambiguous. Anything with zero or several candidates goes
  into `calls_unresolved` on the caller rather than being guessed at: a wrong
  CALLS edge is worse than a missing one, because nothing downstream can tell
  it is wrong.
* No type inference, no import-aliasing, no method dispatch on receiver type.
  That is the Hybrid-LSP layer codebase-memory-mcp has and this does not, and
  it is where to start if these edges ever need to be authoritative.
"""

import ast
import builtins
import logging
import os
from dataclasses import dataclass, field

from app.services import graph_schema as gs

logger = logging.getLogger("app.code_intel")

# `len`, `print`, `str`... a node per unique builtin would wire half the
# codebase to itself and drown the call graph it is meant to make readable.
_BUILTINS = frozenset(dir(builtins))

# tree-sitter node types, by role. Shared across the JS family; a new language
# is a row here plus its grammar, not new walking code.
_TS_CLASS = {"class_declaration", "class_specifier", "class_definition", "class"}
_TS_FUNCTION = {
    "function_declaration",
    "generator_function_declaration",
    "function_definition",
}
_TS_METHOD = {"method_definition", "method_signature"}
_TS_ARROW = {"arrow_function", "function_expression", "generator_function"}


@dataclass(frozen=True)
class Call:
    """One call site. `root` is the receiver for `httpx.get()` -> "httpx", and
    None for a bare `helper()`. The receiver is what lets an unresolved call be
    tied back to an import instead of guessed at."""

    name: str
    line: int
    root: str | None = None

    @property
    def dotted(self) -> str:
        return f"{self.root}.{self.name}" if self.root else self.name


@dataclass
class Symbol:
    kind: str  # gs.CLASS | gs.FUNCTION | gs.METHOD
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str = ""
    bases: list[str] = field(default_factory=list)
    class_qualified_name: str | None = None
    calls: list[Call] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)


@dataclass
class FileSymbols:
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    # Names this file pulled in. An unresolved call whose name (or receiver) is
    # one of these is a library symbol worth a node; anything else is a builtin
    # or a method on a local, and is not.
    imported: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Python: stdlib ast
# --------------------------------------------------------------------------

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _called_names(node: ast.AST) -> list[Call]:
    """Every call site under a node.

    `self.save()` and `db.save()` both yield the name "save" -- which receiver
    it was is exactly the type information this pass does not have, and is why
    an ambiguous name ends up unresolved instead of wired to a guess. The
    receiver is still recorded, because `httpx.get()` naming an imported module
    is knowable without any type inference at all.
    """
    calls: list[Call] = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Name):
            calls.append(Call(func.id, child.lineno))
        elif isinstance(func, ast.Attribute):
            root = func.value.id if isinstance(func.value, ast.Name) else None
            calls.append(Call(func.attr, child.lineno, root))
    return calls


def _extract_python(source: str) -> FileSymbols:
    tree = ast.parse(source)
    out = FileSymbols()

    for node in tree.body:
        if isinstance(node, ast.Import):
            out.imports += [alias.name for alias in node.names]
            # `import os.path as p` binds "p"; `import os` binds "os".
            out.imported.update(
                {(alias.asname or alias.name.split(".")[0]): alias.name
                 for alias in node.names}
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out.imports.append(node.module)
            out.imported.update(
                {(alias.asname or alias.name): node.module or ""
                 for alias in node.names}
            )
        elif isinstance(node, ast.ClassDef):
            out.symbols.append(
                Symbol(
                    kind=gs.CLASS,
                    name=node.name,
                    qualified_name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    bases=[
                        base.id if isinstance(base, ast.Name) else _dotted(base)
                        for base in node.bases
                    ],
                )
            )
            for item in node.body:
                if isinstance(item, _FUNCTION_NODES):
                    out.symbols.append(
                        Symbol(
                            kind=gs.METHOD,
                            name=item.name,
                            qualified_name=f"{node.name}.{item.name}",
                            start_line=item.lineno,
                            end_line=getattr(item, "end_lineno", item.lineno),
                            signature=f"{item.name}({ast.unparse(item.args)})",
                            class_qualified_name=node.name,
                            calls=_called_names(item),
                        )
                    )
        elif isinstance(node, _FUNCTION_NODES):
            out.symbols.append(
                Symbol(
                    kind=gs.FUNCTION,
                    name=node.name,
                    qualified_name=node.name,
                    start_line=node.lineno,
                    end_line=getattr(node, "end_lineno", node.lineno),
                    signature=f"{node.name}({ast.unparse(node.args)})",
                    calls=_called_names(node),
                )
            )
    return out


def _dotted(node: ast.AST) -> str:
    """`a.b.C` from an Attribute chain; the bare tail is enough to match on."""
    try:
        return ast.unparse(node)
    except Exception:
        return getattr(node, "attr", "")


# --------------------------------------------------------------------------
# Everything else: tree-sitter
# --------------------------------------------------------------------------


def _parser_for(language: str):
    """The grammar, or None if the optional extra is not installed."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(language)
    except Exception:
        logger.debug("No tree-sitter grammar for %s", language)
        return None


def _text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _named_child(node, field_name: str):
    try:
        return node.child_by_field_name(field_name)
    except Exception:
        return None


def _ts_calls(node, source: bytes) -> list[Call]:
    calls: list[Call] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == "call_expression":
            func = _named_child(current, "function")
            line = current.start_point[0] + 1
            if func is not None:
                if func.type == "member_expression":
                    prop = _named_child(func, "property")
                    obj = _named_child(func, "object")
                    if prop is not None:
                        root = (
                            _text(obj, source)
                            if obj is not None and obj.type == "identifier"
                            else None
                        )
                        calls.append(Call(_text(prop, source), line, root))
                elif func.type in ("identifier", "shorthand_property_identifier"):
                    calls.append(Call(_text(func, source), line))
        stack.extend(current.children)
    return calls


def _extract_tree_sitter(source: str, language: str) -> FileSymbols:
    parser = _parser_for(language)
    out = FileSymbols()
    if parser is None:
        return out

    data = source.encode("utf-8")
    root = parser.parse(data).root_node

    def walk(node, class_name: str | None, in_function: bool) -> None:
        for child in node.children:
            kind = child.type

            if kind in ("import_statement", "import_declaration"):
                target = _named_child(child, "source")
                if target is not None:
                    out.imports.append(_text(target, data).strip("\"'`"))
                # Every identifier bound by the import clause: default import,
                # namespace alias, and each named binding.
                stack = [child]
                while stack:
                    current = stack.pop()
                    if current.type == "identifier" and current is not target:
                        out.imported[_text(current, data)] = (
                            _text(target, data).strip("\"'`") if target else ""
                        )
                    stack.extend(current.children)
                continue

            if kind in _TS_CLASS:
                name_node = _named_child(child, "name")
                name = _text(name_node, data) if name_node is not None else "(anonymous)"
                bases, implements = [], []
                heritage = _named_child(child, "superclass")
                if heritage is not None:
                    bases.append(_text(heritage, data))
                # TS distinguishes `extends` from `implements`; Python has no
                # interface concept, so this branch never runs for Python.
                for part in child.children:
                    if part.type == "class_heritage":
                        for clause in part.children:
                            names = [
                                _text(n, data)
                                for n in clause.children
                                if n.type in ("identifier", "type_identifier")
                            ]
                            if clause.type == "implements_clause":
                                implements += names
                            elif clause.type == "extends_clause":
                                bases += names
                out.symbols.append(
                    Symbol(
                        kind=gs.CLASS,
                        name=name,
                        qualified_name=name,
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        bases=bases,
                        implements=implements,
                    )
                )
                walk(child, name, False)
                continue

            if kind in _TS_METHOD and class_name:
                name_node = _named_child(child, "name")
                name = _text(name_node, data) if name_node is not None else "(anonymous)"
                params = _named_child(child, "parameters")
                out.symbols.append(
                    Symbol(
                        kind=gs.METHOD,
                        name=name,
                        qualified_name=f"{class_name}.{name}",
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        signature=f"{name}{_text(params, data) if params else '()'}",
                        class_qualified_name=class_name,
                        calls=_ts_calls(child, data),
                    )
                )
                continue

            if kind in _TS_FUNCTION and not in_function:
                name_node = _named_child(child, "name")
                name = _text(name_node, data) if name_node is not None else "(anonymous)"
                params = _named_child(child, "parameters")
                out.symbols.append(
                    Symbol(
                        kind=gs.FUNCTION,
                        name=name,
                        qualified_name=name,
                        start_line=child.start_point[0] + 1,
                        end_line=child.end_point[0] + 1,
                        signature=f"{name}{_text(params, data) if params else '()'}",
                        calls=_ts_calls(child, data),
                    )
                )
                continue

            # `const useThing = () => {...}` -- the dominant shape in modern
            # JS/TS, and invisible if only function_declaration is looked for.
            if kind == "variable_declarator" and not in_function:
                value = _named_child(child, "value")
                name_node = _named_child(child, "name")
                if value is not None and value.type in _TS_ARROW and name_node is not None:
                    name = _text(name_node, data)
                    params = _named_child(value, "parameters")
                    out.symbols.append(
                        Symbol(
                            kind=gs.FUNCTION,
                            name=name,
                            qualified_name=name,
                            start_line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                            signature=f"{name}{_text(params, data) if params else '()'}",
                            calls=_ts_calls(value, data),
                        )
                    )
                    continue

            walk(child, class_name, in_function)

    walk(root, None, False)
    return out


def extract(source: str, language: str) -> FileSymbols:
    """One file's symbols. Never raises -- a file that will not parse yields
    nothing, because one syntax error must not abandon a folder ingest."""
    try:
        if language == "python":
            return _extract_python(source)
        return _extract_tree_sitter(source, language)
    except (SyntaxError, ValueError, RecursionError) as e:
        logger.debug("Could not parse %s source: %s", language, e)
        return FileSymbols()


# --------------------------------------------------------------------------
# Resolution and graph writing
# --------------------------------------------------------------------------


def symbol_node(file_node: str, qualified_name: str) -> str:
    return f"{file_node}::{qualified_name}"


def _import_target(spec: str, file_rel: str, by_rel_path: dict[str, str]) -> str | None:
    """Which file in the tree an import refers to, or None if it leaves it.

    Relative JS specifiers are resolved against the importing file; dotted
    Python module paths are matched against the tree's own paths. Package
    resolution, aliases and tsconfig paths are not attempted -- an import that
    does not land exactly is recorded as external rather than guessed.
    """
    if spec.startswith("."):
        base = os.path.normpath(
            os.path.join(os.path.dirname(file_rel), spec)
        ).replace(os.sep, "/")
        candidates = (
            [base]
            + [f"{base}{ext}" for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".py")]
            + [f"{base}/index{ext}" for ext in (".ts", ".tsx", ".js", ".jsx")]
        )
    else:
        dotted = spec.replace(".", "/")
        here = os.path.dirname(file_rel)
        # Root-relative first (`from app.services import x` from the tree
        # root), then relative to the importing file's own directory, which is
        # how a flat package's `from util import helper` actually resolves.
        candidates = [f"{dotted}.py", f"{dotted}/__init__.py", dotted]
        if here:
            candidates += [
                f"{here}/{dotted}.py",
                f"{here}/{dotted}/__init__.py",
                f"{here}/{dotted}",
            ]
    for candidate in candidates:
        if candidate in by_rel_path:
            return by_rel_path[candidate]

    # Last resort: a unique path SUFFIX, trying progressively shorter tails of
    # the module path. A scan almost never starts at the sys.path root -- point
    # it at `backend/app` and every `from app.services.x import y` in the tree
    # has a leading component that no file path under it can match. Longest
    # tail first so the most specific answer wins, and a tail matching more
    # than one file resolves to nothing: a coin flip is not an import edge.
    parts = spec.replace(".", "/").split("/")
    for start in range(len(parts)):
        tail = "/".join(parts[start:])
        for candidate in (f"{tail}.py", f"{tail}/__init__.py"):
            matches = [
                node
                for rel, node in by_rel_path.items()
                if rel == candidate or rel.endswith(f"/{candidate}")
            ]
            if len(matches) == 1:
                return matches[0]
            if matches:
                return None
    return None


def build_index(parsed: dict[str, FileSymbols]) -> dict[str, list[str]]:
    """Simple name -> every symbol node that answers to it, project-wide.

    A name with more than one entry is what makes a call unresolvable; keeping
    the list (rather than a first-wins map) is what lets that be detected
    instead of silently picking one.
    """
    index: dict[str, list[str]] = {}
    for file_node, symbols in parsed.items():
        for symbol in symbols.symbols:
            if symbol.kind == gs.CLASS:
                continue
            index.setdefault(symbol.name, []).append(
                symbol_node(file_node, symbol.qualified_name)
            )
    return index


def resolve_call(
    name: str, local: dict[str, list[str]], index: dict[str, list[str]]
) -> str | None:
    """File-first, then project-wide, and only when unambiguous.

    Both scopes hold a LIST, and both require exactly one candidate. A dict of
    name -> node here would silently let the last definition win: a file with
    `Base.save` and `Child.save` would resolve every bare `save()` to whichever
    class was parsed second -- an edge indistinguishable from a correct one and
    wrong half the time.
    """
    for candidates in (local.get(name, []), index.get(name, [])):
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None  # ambiguous in this scope; an outer scope cannot fix it
    return None


def external_node(name: str) -> str:
    return f"external:{name}"


def is_external_symbol(call: Call, imported: dict[str, str]) -> bool:
    """Whether an unresolved call deserves an ExternalSymbol node.

    The rule: it must be traceable to an import. `httpx.get()` where `httpx`
    was imported is a real library call worth seeing in the graph. `len()`,
    `items()`, `self.save()` are builtins and methods on locals -- a node per
    unique one of those would add hundreds of hubs like `len` and `append`
    wired to half the codebase, making the call graph less readable, not more.

    ponytail: an import is the only signal available without type inference, so
    a library call reached through an un-imported alias is missed and stays in
    `calls_unresolved`. Revisit if a type-resolution layer ever lands.
    """
    if call.root is not None:
        return call.root in imported
    return call.name in imported and call.name not in _BUILTINS


@dataclass
class CallEdge:
    caller: str
    target: str
    line: int
    resolved: bool
    confidence: float
    count: int = 1


def plan_calls(
    parsed: dict[str, FileSymbols], index: dict[str, list[str]]
) -> tuple[list[CallEdge], dict[str, str], dict[str, list[str]]]:
    """Resolve every call site before anything is written.

    Separated from the write pass because `calls_in_count` needs the whole
    project's edges before the first node can be given its properties.

    Returns the edges, the external symbols to create (node id -> module
    guess), and the names that stayed unresolved, per caller.
    """
    edges: dict[tuple[str, str], CallEdge] = {}
    externals: dict[str, str] = {}
    unresolved: dict[str, set[str]] = {}

    for file_node, symbols in parsed.items():
        local: dict[str, list[str]] = {}
        for symbol in symbols.symbols:
            if symbol.kind != gs.CLASS:
                local.setdefault(symbol.name, []).append(
                    symbol_node(file_node, symbol.qualified_name)
                )

        for symbol in symbols.symbols:
            caller = symbol_node(file_node, symbol.qualified_name)
            for call in symbol.calls:
                target = resolve_call(call.name, local, index)
                if target is not None and target != caller:
                    # A file-local hit is a certainty; a project-wide unique
                    # name is a strong guess a same-named import could fool.
                    confidence = 1.0 if call.name in local else 0.8
                    resolved = True
                elif target is not None:
                    continue  # self-recursion would be a self-loop
                elif is_external_symbol(call, symbols.imported):
                    target = external_node(call.dotted)
                    externals[target] = symbols.imported.get(
                        call.root or call.name, ""
                    )
                    confidence, resolved = 0.0, False
                else:
                    unresolved.setdefault(caller, set()).add(call.dotted)
                    continue

                # The store keeps one edge per node pair, so repeated calls to
                # the same target collapse: keep the first line and count them.
                existing = edges.get((caller, target))
                if existing is None:
                    edges[(caller, target)] = CallEdge(
                        caller, target, call.line, resolved, confidence
                    )
                else:
                    existing.count += 1
                    existing.line = min(existing.line, call.line)

    return (
        list(edges.values()),
        externals,
        {caller: sorted(names) for caller, names in unresolved.items()},
    )


async def project(
    rag,
    parsed: dict[str, FileSymbols],
    file_meta: dict[str, dict],
    source_name: str,
    doc_id: str,
) -> dict:
    """Write every file's symbols and the edges between them.

    `parsed` and `file_meta` are keyed by CodeFile node id. Calls are planned
    across the whole tree first: a call in the first file may land in the last,
    and `calls_in_count` is not knowable until every edge is.
    """
    index = build_index(parsed)
    call_edges, externals, unresolved = plan_calls(parsed, index)
    by_rel_path = {
        meta["rel_path"]: file_node for file_node, meta in file_meta.items()
    }

    out_count: dict[str, int] = {}
    in_count: dict[str, int] = {}
    for edge in call_edges:
        out_count[edge.caller] = out_count.get(edge.caller, 0) + 1
        in_count[edge.target] = in_count.get(edge.target, 0) + 1

    counts = {
        "classes": 0,
        "functions": 0,
        "methods": 0,
        "calls": len(call_edges),
        "external_symbols": len(externals),
        "unresolved": sum(len(names) for names in unresolved.values()),
    }

    for node, module_guess in externals.items():
        name = node.removeprefix("external:")
        origin = f" (from {module_guess})." if module_guess else "."
        await gs.upsert_node(
            rag,
            node,
            gs.EXTERNAL_SYMBOL,
            description=(
                f"{name}, called from {source_name} but defined outside it{origin}"
            ),
            file_path=source_name,
            source_id=doc_id,
            index=False,
            name=name,
            module_guess=module_guess,
            external=True,
            calls_in_count=in_count.get(node, 0),
            calls_out_count=0,
        )

    for file_node, symbols in parsed.items():
        meta = file_meta[file_node]
        class_nodes = {
            symbol.name: symbol_node(file_node, symbol.qualified_name)
            for symbol in symbols.symbols
            if symbol.kind == gs.CLASS
        }

        for symbol in symbols.symbols:
            node = symbol_node(file_node, symbol.qualified_name)
            properties = {
                "path": meta["rel_path"],
                "qualified_name": symbol.qualified_name,
                "start_line": symbol.start_line,
                "end_line": symbol.end_line,
                "language": meta.get("language") or "",
                "calls_out_count": out_count.get(node, 0),
                "calls_in_count": in_count.get(node, 0),
            }
            if symbol.signature:
                properties["signature"] = symbol.signature
            if symbol.class_qualified_name:
                properties["class_qualified_name"] = symbol.class_qualified_name
            if unresolved.get(node):
                # Kept for backward compatibility, and because "calls something
                # named X we could not place" is real information -- but it is
                # no longer where calls go to disappear: anything traceable to
                # an import is an ExternalSymbol edge instead.
                properties["calls_unresolved"] = ", ".join(unresolved[node])

            await gs.upsert_node(
                rag,
                node,
                symbol.kind,
                description=(
                    f"{symbol.kind} {symbol.qualified_name} in {meta['rel_path']} "
                    f"({source_name}), lines {symbol.start_line}-{symbol.end_line}."
                ),
                file_path=source_name,
                source_id=doc_id,
                # Graph-only: a repo contributes thousands of these and they
                # would swamp the document cards in the entity vector store.
                index=False,
                **properties,
            )

            if symbol.kind == gs.METHOD and symbol.class_qualified_name in class_nodes:
                await gs.upsert_edge(
                    rag,
                    class_nodes[symbol.class_qualified_name],
                    node,
                    gs.DEFINES_METHOD,
                    description=f"{symbol.class_qualified_name} defines {symbol.name}.",
                    file_path=source_name,
                    source_id=doc_id,
                )
            else:
                await gs.upsert_edge(
                    rag,
                    file_node,
                    node,
                    gs.DEFINES,
                    description=f"{meta['rel_path']} defines {symbol.qualified_name}.",
                    file_path=source_name,
                    source_id=doc_id,
                )

            inheritance = [(b, gs.INHERITS) for b in symbol.bases]
            inheritance += [(i, gs.IMPLEMENTS) for i in symbol.implements]
            for base, rel_type in inheritance:
                target = class_nodes.get(base.rsplit(".", 1)[-1])
                if target and target != node:
                    await gs.upsert_edge(
                        rag,
                        node,
                        target,
                        rel_type,
                        description=f"{symbol.name} {rel_type.lower()} {base}.",
                        file_path=source_name,
                        source_id=doc_id,
                    )

            counts[
                {gs.CLASS: "classes", gs.FUNCTION: "functions", gs.METHOD: "methods"}[
                    symbol.kind
                ]
            ] += 1

        for spec in sorted(set(symbols.imports)):
            target = _import_target(spec, meta["rel_path"], by_rel_path)
            if target and target != file_node:
                await gs.upsert_edge(
                    rag,
                    file_node,
                    target,
                    gs.IMPORTS,
                    description=f"{meta['rel_path']} imports {spec}.",
                    file_path=source_name,
                    source_id=doc_id,
                )

    for edge in call_edges:
        callee = edge.target.rsplit("::", 1)[-1].removeprefix("external:")
        repeats = f" ({edge.count}x)" if edge.count > 1 else ""
        await gs.upsert_edge(
            rag,
            edge.caller,
            edge.target,
            gs.CALLS,
            description=(
                f"{edge.caller.rsplit('::', 1)[-1]} calls {callee}{repeats}, "
                f"line {edge.line}."
            ),
            file_path=source_name,
            source_id=doc_id,
            call_site_line=edge.line,
            call_count=edge.count,
            resolved=edge.resolved,
            confidence=edge.confidence,
        )

    return counts


if __name__ == "__main__":
    src = """
import os
import httpx
from app.services import manifest
from collections import Counter

class Base:
    def save(self):
        return helper()

class Child(Base):
    def save(self):
        return os.getcwd()

def helper():
    return 1

def main():
    total = len([1, 2])
    Counter(total)
    httpx.get("http://x")
    return helper()
"""
    result = extract(src, "python")
    kinds = {(s.kind, s.qualified_name) for s in result.symbols}
    assert (gs.CLASS, "Base") in kinds and (gs.CLASS, "Child") in kinds
    assert (gs.METHOD, "Base.save") in kinds and (gs.FUNCTION, "helper") in kinds
    assert result.imports == ["os", "httpx", "app.services", "collections"]
    assert result.imported["manifest"] == "app.services"
    assert result.imported["httpx"] == "httpx"

    main = next(s for s in result.symbols if s.name == "main")
    assert main.signature == "main()"
    assert {c.dotted for c in main.calls} == {"len", "Counter", "httpx.get", "helper"}
    assert all(c.line > 0 for c in main.calls)

    parsed = {"proj/a.py": result}
    index = build_index(parsed)
    # Base.save and Child.save both answer to "save": ambiguous, so unresolved.
    assert len(index["save"]) == 2
    assert resolve_call("save", {}, index) is None
    assert resolve_call("helper", {}, index) == "proj/a.py::helper"
    # File-local wins when IT is unambiguous...
    assert resolve_call("save", {"save": ["proj/a.py::Base.save"]}, index) == (
        "proj/a.py::Base.save"
    )
    # ...and an ambiguous local scope resolves to nothing rather than to
    # whichever definition happened to be parsed last.
    assert resolve_call("save", {"save": ["a::Base.save", "a::Child.save"]}, index) is (
        None
    )

    edges, externals, unresolved = plan_calls(parsed, index)
    by_pair = {(e.caller, e.target): e for e in edges}
    # Resolved, in-file: full confidence.
    assert by_pair[("proj/a.py::main", "proj/a.py::helper")].confidence == 1.0
    assert by_pair[("proj/a.py::main", "proj/a.py::helper")].resolved is True
    # Imported names become real edges instead of vanishing into a string.
    assert "external:httpx.get" in externals and externals["external:httpx.get"] == "httpx"
    assert "external:Counter" in externals
    assert externals["external:Counter"] == "collections"
    assert not by_pair[("proj/a.py::main", "external:httpx.get")].resolved
    # A builtin does NOT get a node -- `len` as a hub helps nobody.
    assert "external:len" not in externals
    assert "len" in unresolved["proj/a.py::main"]

    assert is_external_symbol(Call("get", 1, "httpx"), {"httpx": "httpx"})
    assert not is_external_symbol(Call("get", 1, "self"), {"httpx": "httpx"})
    assert not is_external_symbol(Call("len", 1), {"len": "shadowed"}), "builtin"

    tree = {"src/util.py": "proj/src/util.py", "src/app.py": "proj/src/app.py"}
    assert _import_target("src.util", "src/app.py", tree) == "proj/src/util.py"
    assert _import_target("./util", "src/app.py", tree) == "proj/src/util.py"
    assert _import_target("react", "src/app.py", tree) is None
    # The scan root is inside the package: `app.src.util` still has to land,
    # by dropping the leading component no tree path can match.
    assert _import_target("app.src.util", "src/app.py", tree) == "proj/src/util.py"
    print("ok")
