"""A workbook's structure must reach the graph exactly, and leave again
cleanly -- without taking the document entities it linked to with it."""

import asyncio
import datetime
from io import BytesIO

import openpyxl
import pytest

from app.services import graph_schema as gs
from app.services import spreadsheet_query, tabular_graph
from app.services.test_source_graph import FakeGraph
from app.services.test_source_graph import FakeVdb as _FakeVdb
from app.services.parsers import spreadsheet


def _fixture_workbook() -> bytes:
    wb = openpyxl.Workbook()
    sales = wb.active
    sales.title = "Sales"
    sales.append(["Customer", "Revenue", "Cost", "Order Date", "Profit"])
    rows = [
        ("Acme Corp", 1200.50, 800.0, datetime.date(2025, 1, 14)),
        ("Globex", 900.00, 400.0, datetime.date(2025, 2, 3)),
    ]
    for index, row in enumerate(rows, start=2):
        sales.append([*row, f"=B{index}-C{index}"])
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# The graph/vdb fakes live in test_source_graph -- one definition, so a
# storage contract (like "commit the write") can never be satisfied by one
# copy and quietly missing from the other.
class FakeVdb(_FakeVdb):
    """Adds retrieval, which only the routing test needs."""

    def __init__(self):
        super().__init__()
        self.hits: list[dict] = []

    async def query(self, question, top_k):
        return self.hits


class FakeRag:
    def __init__(self):
        self.chunk_entity_relation_graph = FakeGraph()
        self.entities_vdb = FakeVdb()


@pytest.fixture
def projected(tmp_path, monkeypatch):
    monkeypatch.setattr(spreadsheet._settings, "storage_dir", str(tmp_path))
    spreadsheet.close_connections()
    workbook = spreadsheet.load_spreadsheet(_fixture_workbook(), "quarterly.xlsx")

    rag = FakeRag()
    # A document already talked about this customer; the spreadsheet should
    # find it rather than create a second node for the same thing.
    rag.chunk_entity_relation_graph.nodes["ACME CORP"] = {
        "entity_id": "ACME CORP",
        "entity_type": "organization",
    }
    asyncio.run(tabular_graph.project(rag, workbook, "doc-1"))
    yield rag, workbook
    spreadsheet.close_connections()


def test_structure_is_written_with_labels_and_typed_edges(projected):
    rag, workbook = projected
    graph = rag.chunk_entity_relation_graph
    table = workbook["sheets"][0]["table"]

    assert graph.nodes["quarterly.xlsx"]["entity_type"] == gs.WORKBOOK
    sheet = "quarterly.xlsx:Sales"
    assert graph.nodes[sheet]["entity_type"] == gs.WORKSHEET
    assert graph.nodes[sheet]["table"] == table

    revenue = "quarterly.xlsx:Sales.revenue"
    assert graph.nodes[revenue]["entity_type"] == gs.COLUMN
    assert graph.nodes[revenue]["column"] == "revenue"

    assert graph.edges[("quarterly.xlsx", sheet)]["keywords"] == gs.HAS_SHEET
    assert graph.edges[(sheet, revenue)]["keywords"] == gs.HAS_COLUMN
    # The formula, not a guess: Profit = Revenue - Cost.
    profit = "quarterly.xlsx:Sales.profit"
    assert graph.edges[(profit, revenue)]["keywords"] == gs.DERIVED_FROM
    assert graph.edges[(profit, "quarterly.xlsx:Sales.cost")]["keywords"] == gs.DERIVED_FROM

    # Every structural node is retrievable.
    assert len(rag.entities_vdb.records) == len(graph.nodes) - 1  # minus ACME CORP


def test_cell_values_link_to_documents_but_never_create_nodes(projected):
    rag, _ = projected
    graph = rag.chunk_entity_relation_graph
    customer = "quarterly.xlsx:Sales.customer"

    # The cell says "Acme Corp"; the document's node is ACME CORP. The link
    # only happens because both sides go through the same canonical name.
    assert graph.edges[(customer, "ACME CORP")]["keywords"] == gs.HAS_VALUE
    assert graph.nodes["ACME CORP"]["entity_type"] == "organization", (
        "a spreadsheet must not relabel an entity a document already typed"
    )
    assert "GLOBEX" not in graph.nodes and "Globex" not in graph.nodes, (
        "a value no document mentions has nothing to link to and stays out"
    )


def test_removal_takes_the_structure_and_leaves_the_entities(projected):
    rag, _ = projected
    graph = rag.chunk_entity_relation_graph

    asyncio.run(tabular_graph.remove(rag, "quarterly.xlsx"))

    assert list(graph.nodes) == ["ACME CORP"]
    assert graph.edges == {}
    assert rag.entities_vdb.records == {}


def test_routing_picks_the_table_the_question_retrieved(projected):
    rag, workbook = projected
    table = workbook["sheets"][0]["table"]
    rag.entities_vdb.hits = [
        {"entity_name": "Acme Corp"},
        {"entity_name": "quarterly.xlsx:Sales.revenue"},
    ]
    assert asyncio.run(spreadsheet_query.relevant_tables(rag, "total revenue")) == [table]

    rag.entities_vdb.hits = [{"entity_name": "Acme Corp"}]
    assert asyncio.run(spreadsheet_query.relevant_tables(rag, "who is Acme")) == [], (
        "a question that retrieves no tabular node must not reach the SQL path"
    )
