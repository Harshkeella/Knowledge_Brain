"""Spreadsheets must land in DuckDB with their real types, and the LLM must
never be able to run anything but a validated SELECT over them."""

import asyncio
import datetime
from io import BytesIO

import openpyxl
import pytest

from app.services import spreadsheet_query
from app.services.parsers import spreadsheet


def _fixture_workbook() -> bytes:
    """Excel's own conventions: currency and percentage are number formats over
    plain floats, and a formula column is a string until Excel evaluates it."""
    wb = openpyxl.Workbook()
    sales = wb.active
    sales.title = "Sales"
    sales.append(["Customer", "Region", "Revenue", "Cost", "Margin", "Order Date", "Profit"])
    rows = [
        ("Acme Corp", "North", 1200.50, 800.0, 0.334, datetime.date(2025, 1, 14)),
        ("Globex", "South", 900.00, 400.0, 0.555, datetime.date(2025, 2, 3)),
        ("Initech", "North", 450.25, 300.0, 0.333, datetime.date(2025, 2, 27)),
        ("Umbrella", "South", 2200.00, 1500.0, 0.318, datetime.date(2025, 3, 9)),
    ]
    for index, row in enumerate(rows, start=2):
        sales.append([*row, f"=C{index}-D{index}"])
        sales[f"C{index}"].number_format = '"$"#,##0.00'
        sales[f"D{index}"].number_format = '"$"#,##0.00'
        sales[f"E{index}"].number_format = "0.0%"
        sales[f"F{index}"].number_format = "yyyy-mm-dd"

    regions = wb.create_sheet("Regions")
    regions.append(["Region", "Manager"])
    regions.append(["North", "Dana Whitfield"])
    regions.append(["South", "Sam Okonkwo"])

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(spreadsheet._settings, "storage_dir", str(tmp_path))
    spreadsheet.close_connections()
    yield
    spreadsheet.close_connections()


@pytest.fixture
def workbook(store):
    return spreadsheet.load_spreadsheet(_fixture_workbook(), "quarterly.xlsx")


def _columns(workbook, worksheet):
    sheet = next(s for s in workbook["sheets"] if s["worksheet"] == worksheet)
    return sheet, {c["header"]: c for c in sheet["columns"]}


# --- Ingestion -------------------------------------------------------------


def test_every_worksheet_becomes_its_own_duckdb_table(workbook):
    assert [s["worksheet"] for s in workbook["sheets"]] == ["Sales", "Regions"]
    con = spreadsheet.get_connection()
    for sheet in workbook["sheets"]:
        count = con.execute(f'SELECT count(*) FROM "{sheet["table"]}"').fetchone()[0]
        assert count == sheet["rows"]


def test_number_formats_decide_the_type_not_the_raw_value(workbook):
    _, columns = _columns(workbook, "Sales")
    assert (columns["Revenue"]["semantic"], columns["Revenue"]["data_type"]) == (
        "currency",
        "DOUBLE",
    )
    assert columns["Margin"]["semantic"] == "percentage"
    assert columns["Order Date"]["data_type"] in ("DATE", "TIMESTAMP")
    assert columns["Region"]["semantic"] == "categorical"
    assert columns["Customer"]["semantic"] == "text"


def test_values_survive_exactly(workbook):
    sheet, _ = _columns(workbook, "Sales")
    total = spreadsheet.get_connection().execute(
        f'SELECT sum(revenue) FROM "{sheet["table"]}"'
    ).fetchone()[0]
    assert total == pytest.approx(4750.75)


def test_formula_lineage_is_kept(workbook):
    _, columns = _columns(workbook, "Sales")
    # openpyxl-written workbooks carry no cached formula results, so the column
    # is empty here -- the lineage is what matters and it comes from the formula.
    assert columns["Profit"]["formula"] == "=C2-D2"
    assert columns["Profit"]["derived_from"] == ["Cost", "Revenue"]


def test_summary_is_an_index_entry_not_a_second_copy_of_the_schema(workbook):
    """It exists to give the document a body. The columns, types and formula
    lineage live in the graph, written by tabular_graph -- prose repeating them
    is what turned VARCHAR and BIGINT into graph nodes."""
    summary = spreadsheet.build_summary(workbook)
    assert summary.startswith(spreadsheet.SUMMARY_HEADER), "the extractor skips on this"
    assert "quarterly.xlsx" in summary and "Sales" in summary and "4 rows" in summary
    assert "Acme Corp" not in summary and "1200.5" not in summary, "no cells"
    for jargon in ("VARCHAR", "BIGINT", "currency", "categorical"):
        assert jargon not in summary, f"{jargon} must not reach the extractor"


def test_reuploading_replaces_rather_than_duplicates(workbook):
    reloaded = spreadsheet.load_spreadsheet(_fixture_workbook(), "quarterly.xlsx")
    assert sorted(spreadsheet_query.list_tables()) == sorted(
        s["table"] for s in reloaded["sheets"]
    )


def test_deleting_the_workbook_drops_its_tables(workbook):
    spreadsheet.drop_workbook_tables("quarterly.xlsx")
    assert spreadsheet_query.list_tables() == []


# --- Query guardrails ------------------------------------------------------


def test_only_select_is_allowed(workbook):
    table = workbook["sheets"][0]["table"]
    for sql in [f'DROP TABLE "{table}"', f'UPDATE "{table}" SET revenue = 0']:
        with pytest.raises(spreadsheet_query.SpreadsheetError, match="SELECT"):
            spreadsheet_query.run_select(sql)
    # And the data is untouched.
    assert spreadsheet_query.run_select(f'SELECT count(*) FROM "{table}"')["rows"] == [[4]]


def test_multiple_statements_are_rejected(workbook):
    table = workbook["sheets"][0]["table"]
    with pytest.raises(spreadsheet_query.SpreadsheetError, match="single statement"):
        spreadsheet_query.run_select(f'SELECT 1; DROP TABLE "{table}"')


def test_hallucinated_names_are_rejected_before_execution(workbook):
    table = workbook["sheets"][0]["table"]
    with pytest.raises(spreadsheet_query.SpreadsheetError, match="does not exist"):
        spreadsheet_query.run_select(f'SELECT margin_pct_2 FROM "{table}"')
    with pytest.raises(spreadsheet_query.SpreadsheetError, match="does not exist"):
        spreadsheet_query.run_select("SELECT * FROM workbook_nope__sales")


def test_generated_sql_cannot_reach_the_filesystem(workbook):
    with pytest.raises(spreadsheet_query.SpreadsheetError):
        spreadsheet_query.run_select("SELECT * FROM read_csv_auto('/etc/passwd')")


def test_results_are_capped(workbook, monkeypatch):
    monkeypatch.setattr(spreadsheet_query._settings, "spreadsheet_max_rows", 2)
    table = workbook["sheets"][0]["table"]
    result = spreadsheet_query.run_select(f'SELECT * FROM "{table}"')
    assert result["total_row_count"] == 2
    assert result["truncated"] is True


# --- Computed columns ------------------------------------------------------


def test_computed_column_is_added_and_revertible(workbook):
    table = workbook["sheets"][0]["table"]
    result = spreadsheet_query.add_computed_column(table, "net", "revenue - cost")
    assert result["added_column"] == "net"
    assert "net" in result["columns"]
    assert result["rows"][0][result["columns"].index("net")] == pytest.approx(400.5)

    assert spreadsheet_query.drop_computed_column(table, "net") is True
    assert "net" not in spreadsheet_query.run_select(f'SELECT * FROM "{table}"')["columns"]
    # An original column is not removable through the undo path.
    assert spreadsheet_query.drop_computed_column(table, "revenue") is False


def test_computed_column_expression_is_validated(workbook):
    table = workbook["sheets"][0]["table"]
    with pytest.raises(spreadsheet_query.SpreadsheetError, match="Invalid column expression"):
        spreadsheet_query.add_computed_column(table, "bad", "revenue - nonexistent")


# --- Routing and self-healing ----------------------------------------------


def _stub_llm(monkeypatch, *answers):
    calls = []

    async def fake(prompt, system_prompt=None, **kwargs):
        calls.append(prompt)
        return answers[min(len(calls) - 1, len(answers) - 1)]

    monkeypatch.setattr(spreadsheet_query, "query_llm_func", fake)
    return calls


def test_non_spreadsheet_questions_fall_through(workbook, monkeypatch):
    _stub_llm(monkeypatch, "NO_SQL")
    assert asyncio.run(spreadsheet_query.answer("who signed the vendor contract?")) is None


def test_bad_sql_is_retried_with_the_error_fed_back(workbook, monkeypatch):
    table = workbook["sheets"][0]["table"]
    calls = _stub_llm(
        monkeypatch,
        f'SELECT region, avg(revenu) FROM "{table}" GROUP BY region',
        f'SELECT region, avg(revenue) AS avg_revenue FROM "{table}" GROUP BY region',
    )

    result = asyncio.run(spreadsheet_query.answer("average revenue by region"))

    assert len(calls) == 2
    assert "error" in calls[1] and "revenu" in calls[1]
    assert sorted(result["rows"]) == [["North", pytest.approx(825.375)], ["South", pytest.approx(1550.0)]]


def test_persistently_bad_sql_gives_up_with_a_clear_message(workbook, monkeypatch):
    _stub_llm(monkeypatch, "SELECT nope FROM nowhere")
    with pytest.raises(spreadsheet_query.SpreadsheetError, match="Couldn't turn that into"):
        asyncio.run(spreadsheet_query.answer("total revenue"))


def test_add_column_request_routes_to_the_write_path(workbook, monkeypatch):
    table = workbook["sheets"][0]["table"]
    _stub_llm(monkeypatch, f"ADD COLUMN {table}.profit_calc = revenue - cost")

    result = asyncio.run(spreadsheet_query.answer("add a profit column = revenue minus cost"))

    assert result["added_column"] == "profit_calc"
    assert spreadsheet_query.drop_computed_column(table, "profit_calc") is True
