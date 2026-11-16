from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from openpyxl import Workbook

from src.exceptions import DatasetValidationError, IngestionError
from src.ingestion.excel_reader import ExcelReader


def write_workbook(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    """Create synthetic workbooks without relying on repository data."""
    workbook = Workbook(write_only=True)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(name)
        for row in rows:
            worksheet.append(row)
    workbook.save(path)
    workbook.close()


def test_lists_and_selects_worksheets(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(
        path,
        {
            "Customers": [["id"], ["001"]],
            "Orders": [["order_id"], ["A001"]],
        },
    )
    reader = ExcelReader()

    assert reader.list_sheets(path) == ("Customers", "Orders")
    batches = list(reader.read_batches(path, sheet_name="Orders"))
    assert pa.Table.from_batches(batches).to_pylist() == [{"order_id": "A001"}]


def test_serializes_values_and_preserves_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(
        path,
        {
            "Data": [
                ["id", "amount", "active", "day", "note"],
                ["001", 12.5, True, datetime(2026, 1, 2), "NA"],
                ["001", 12.5, True, datetime(2026, 1, 2), "NA"],
                ["002", 123, False, None, None],
            ]
        },
    )
    original = path.read_bytes()

    batches = list(ExcelReader().read_batches(path, batch_size=2))
    table = pa.Table.from_batches(batches)

    assert len(batches) == 2
    assert table.to_pylist() == [
        {
            "id": "001",
            "amount": "12.5",
            "active": "true",
            "day": "2026-01-02T00:00:00",
            "note": "NA",
        },
        {
            "id": "001",
            "amount": "12.5",
            "active": "true",
            "day": "2026-01-02T00:00:00",
            "note": "NA",
        },
        {
            "id": "002",
            "amount": "123",
            "active": "false",
            "day": None,
            "note": None,
        },
    ]
    assert all(field.type == pa.string() for field in table.schema)
    assert path.read_bytes() == original


def test_preserves_header_only_worksheet(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(path, {"Data": [["id", "name"]]})

    batches = list(ExcelReader().read_batches(path))
    table = pa.Table.from_batches(batches)

    assert table.num_rows == 0
    assert table.column_names == ["id", "name"]


def test_preserves_empty_records_between_populated_records(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(path, {"Data": [["id", "name"], ["001", "Alice"], [None, None], ["002", "Bob"]]})

    batches = list(ExcelReader().read_batches(path))

    assert pa.Table.from_batches(batches).to_pylist() == [
        {"id": "001", "name": "Alice"},
        {"id": None, "name": None},
        {"id": "002", "name": "Bob"},
    ]


@pytest.mark.parametrize(
    "header",
    [
        ["id", "id"],
        ["id", "   "],
        ["id", 123],
    ],
)
def test_rejects_invalid_headers(tmp_path: Path, header: list[Any]) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(path, {"Data": [header, ["001", "Alice"]]})

    with pytest.raises(DatasetValidationError):
        list(ExcelReader().read_batches(path))


@pytest.mark.parametrize(
    ("value", "message"),
    [("=1+1", "Formula found"), ("#DIV/0!", "Excel error found")],
)
def test_rejects_formulas_and_excel_errors(tmp_path: Path, value: str, message: str) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(path, {"Data": [["id", "value"], ["001", value]]})

    with pytest.raises(IngestionError, match=message):
        list(ExcelReader().read_batches(path))


def test_rejects_unknown_worksheet(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_workbook(path, {"Data": [["id"], ["001"]]})

    with pytest.raises(IngestionError, match="does not exist"):
        list(ExcelReader().read_batches(path, sheet_name="Unknown"))


@pytest.mark.parametrize("batch_size", [0, -1])
def test_rejects_invalid_batch_size(tmp_path: Path, batch_size: int) -> None:
    with pytest.raises(IngestionError, match="Batch size"):
        list(ExcelReader().read_batches(tmp_path / "unused.xlsx", batch_size=batch_size))


def test_rejects_missing_workbook(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="Unable to read Excel"):
        list(ExcelReader().read_batches(tmp_path / "missing.xlsx"))


def test_rejects_invalid_workbook(tmp_path: Path) -> None:
    path = tmp_path / "invalid.xlsx"
    path.write_bytes(b"Not an Excel workbook.")

    with pytest.raises(IngestionError, match="Unable to read Excel"):
        list(ExcelReader().read_batches(path))
