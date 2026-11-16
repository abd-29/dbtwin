from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.exceptions import DatasetValidationError, DbtwinError
from src.models import KeyedComparison, UnkeyedComparison
from src.services.comparison_service import ComparisonService, CsvInput


def test_compares_targets_independently(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    changed = tmp_path / "changed.csv"
    source.write_text("id,city\n001,Rennes\n", encoding="utf-8")
    changed.write_text("id,city\n001,Paris\n", encoding="utf-8")

    job = ComparisonService(tmp_path / "jobs").run(
        source=CsvInput(source),
        targets={
            "unreadable": CsvInput(tmp_path / "missing.csv"),
            "identical": CsvInput(source),
            "changed": CsvInput(changed),
        },
        key="id",
    )

    assert job.targets["unreadable"].error is not None
    assert job.targets["unreadable"].comparison is None

    identical = job.targets["identical"].comparison
    assert isinstance(identical, KeyedComparison)
    assert identical.identical is True

    modified = job.targets["changed"].comparison
    assert isinstance(modified, KeyedComparison)
    assert modified.modified_cells == 1
    assert modified.modified_cells_file is not None
    assert pq.read_table(modified.modified_cells_file).to_pylist() == [
        {
            "record_key": "001",
            "column_name": "city",
            "source_value": "Rennes",
            "target_value": "Paris",
            "value_type": "VARCHAR",
        }
    ]
    assert job.source.dataset.parquet_files[0].is_file()


def test_runs_without_a_key_and_preserves_duplicates(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    source.write_text("name\nAlice\nAlice\nBob\n", encoding="utf-8")
    target.write_text("name\nAlice\nCharlie\n", encoding="utf-8")

    job = ComparisonService(tmp_path / "jobs").run(
        source=CsvInput(source, delimiter=","),
        targets={"target": CsvInput(target, delimiter=",")},
    )

    result = job.targets["target"].comparison
    assert isinstance(result, UnkeyedComparison)
    assert job.key is None
    assert result.matched_rows == 1
    assert result.missing_rows == 2
    assert result.added_rows == 1
    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    missing = pq.read_table(result.missing_rows_file).column("name").to_pylist()
    added = pq.read_table(result.added_rows_file).column("name").to_pylist()
    assert sorted(missing) == ["Alice", "Bob"]
    assert added == ["Charlie"]


@pytest.mark.parametrize("failure", ["missing-file", "duplicate-key"])
def test_source_failure_removes_job_workspace(tmp_path: Path, failure: str) -> None:
    source = tmp_path / "source.csv"
    if failure == "duplicate-key":
        source.write_text("id,name\n001,Alice\n001,Bob\n", encoding="utf-8")
    target = tmp_path / "target.csv"
    target.write_text("id,name\n001,Alice\n", encoding="utf-8")
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    existing = jobs_dir / "existing.txt"
    existing.write_text("Keep this file.", encoding="utf-8")

    with pytest.raises(DbtwinError):
        ComparisonService(jobs_dir).run(
            source=CsvInput(source),
            targets={"target": CsvInput(target)},
            key="id",
        )

    assert list(jobs_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "Keep this file."


def test_keeps_independent_reading_settings(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    target = tmp_path / "target.csv"
    source.write_bytes("id;name\n001;Élodie\n".encode("cp1252"))
    target.write_text("id,name\n001,Élodie\n", encoding="utf-8")

    job = ComparisonService(tmp_path / "jobs").run(
        source=CsvInput(source, encoding="cp1252", delimiter=";"),
        targets={"target": CsvInput(target)},
        key="id",
    )

    assert job.source.settings.encoding == "cp1252"
    assert job.source.settings.delimiter == ";"
    prepared = job.targets["target"].prepared
    assert prepared is not None
    assert prepared.settings.encoding == "utf-8"
    assert prepared.settings.delimiter == ","
    result = job.targets["target"].comparison
    assert isinstance(result, KeyedComparison)
    assert result.identical is True


def test_jobs_have_separate_workspaces(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,name\n001,Alice\n", encoding="utf-8")
    service = ComparisonService(tmp_path / "jobs")
    request = CsvInput(source)

    first = service.run(request, {"target": request}, key="id")
    first_file = first.source.dataset.parquet_files[0]
    original = first_file.read_bytes()
    second = service.run(request, {"target": request}, key="id")

    assert first.job_id != second.job_id
    assert first.workspace != second.workspace
    assert first.workspace.is_dir()
    assert second.workspace.is_dir()
    assert first_file.read_bytes() == original
    assert first.workspace in first_file.parents
    assert second.workspace in second.source.dataset.parquet_files[0].parents


def test_rejects_empty_target_selection_before_creating_workspace(tmp_path: Path) -> None:
    jobs_dir = tmp_path / "jobs"

    with pytest.raises(DatasetValidationError, match="At least one target"):
        ComparisonService(jobs_dir).run(
            source=CsvInput(tmp_path / "source.csv"),
            targets={},
        )

    assert not jobs_dir.exists()