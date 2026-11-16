from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from src.models import KeyedComparison
from src.services.comparison_service import ComparisonService, FileInput


def write_excel(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    """Create a workbook containing synthetic worksheets."""
    workbook = Workbook(write_only=True)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    workbook.save(path)
    workbook.close()


@pytest.mark.parametrize("excel_source", [True, False])
def test_compares_excel_and_csv_with_selected_sheet(tmp_path: Path, excel_source: bool) -> None:
    excel_path = tmp_path / "input.xlsx"
    write_excel(
        excel_path,
        {
            "Other": [["other_id"], ["ignore"]],
            "Customers": [["id", "name"], ["001", "Alice"], ["002", "Bob"]],
        },
    )
    csv_path = tmp_path / "input.csv"
    csv_path.write_text("id,name\n001,Alice\n002,Bob\n", encoding="utf-8")
    excel_input = FileInput(excel_path, file_format="excel", sheet_name="Customers")
    csv_input = FileInput(csv_path)

    job = ComparisonService(tmp_path / "jobs").run(
        source=excel_input if excel_source else csv_input,
        targets={"target": csv_input if excel_source else excel_input},
        key="id",
    )

    outcome = job.targets["target"]
    assert outcome.error is None
    assert isinstance(outcome.comparison, KeyedComparison)
    assert outcome.comparison.identical is True

    prepared = job.source if excel_source else outcome.prepared
    assert prepared is not None
    assert prepared.settings.file_format == "excel"
    assert prepared.settings.sheet_name == "Customers"
    assert prepared.dataset.origin.sheet_name == "Customers"


def test_excel_targets_can_use_different_worksheets(tmp_path: Path) -> None:
    path = tmp_path / "versions.xlsx"
    write_excel(
        path,
        {
            "Reference": [["id", "city"], ["001", "Rennes"]],
            "Unchanged": [["id", "city"], ["001", "Rennes"]],
            "Changed": [["id", "city"], ["001", "Paris"]],
        },
    )

    job = ComparisonService(tmp_path / "jobs").run(
        source=FileInput(path, file_format="excel", sheet_name="Reference"),
        targets={
            "same": FileInput(path, file_format="excel", sheet_name="Unchanged"),
            "changed": FileInput(path, file_format="excel", sheet_name="Changed"),
        },
        key="id",
    )

    same = job.targets["same"].comparison
    changed = job.targets["changed"].comparison
    assert isinstance(same, KeyedComparison)
    assert isinstance(changed, KeyedComparison)
    assert same.identical is True
    assert changed.modified_rows == 1
    assert changed.modified_cells == 1


def test_excel_target_errors_do_not_block_other_targets(tmp_path: Path) -> None:
    path = tmp_path / "input.xlsx"
    write_excel(
        path,
        {
            "Valid": [["id", "value"], ["001", "10"]],
            "Formula": [["id", "value"], ["001", "=5+5"]],
        },
    )

    job = ComparisonService(tmp_path / "jobs").run(
        source=FileInput(path, file_format="excel", sheet_name="Valid"),
        targets={
            "missing": FileInput(path, file_format="excel", sheet_name="Unknown"),
            "formula": FileInput(path, file_format="excel", sheet_name="Formula"),
            "valid": FileInput(path, file_format="excel", sheet_name="Valid"),
        },
        key="id",
    )

    assert "does not exist" in (job.targets["missing"].error or "")
    assert "Formula found" in (job.targets["formula"].error or "")
    valid = job.targets["valid"].comparison
    assert isinstance(valid, KeyedComparison)
    assert valid.identical is True
