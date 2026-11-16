from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.models import KeyedComparison
from src.services.comparison_service import ComparisonService, FileInput


@pytest.mark.parametrize("parquet_source", [True, False])
def test_compares_csv_and_parquet_text_columns(tmp_path: Path, parquet_source: bool) -> None:
    csv_path = tmp_path / "input.csv"
    csv_path.write_text("id,name\n001,Alice\n002,Bob\n", encoding="utf-8")
    parquet_path = tmp_path / "input.parquet"
    pq.write_table(
        pa.table({"id": ["001", "002"], "name": ["Alice", "Bob"]}),
        parquet_path,
    )
    csv_input = FileInput(csv_path)
    parquet_input = FileInput(parquet_path, file_format="parquet")

    job = ComparisonService(tmp_path / "jobs").run(
        source=parquet_input if parquet_source else csv_input,
        targets={"target": csv_input if parquet_source else parquet_input},
        key="id",
    )

    outcome = job.targets["target"]
    assert outcome.error is None
    assert isinstance(outcome.comparison, KeyedComparison)
    assert outcome.comparison.identical is True


def test_preserves_parquet_types_and_exports_modified_cells(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    target = tmp_path / "target.parquet"
    pq.write_table(pa.table({"id": ["001"], "age": [25]}), source)
    pq.write_table(pa.table({"id": ["001"], "age": [26]}), target)

    job = ComparisonService(tmp_path / "jobs").run(
        source=FileInput(source, file_format="parquet"),
        targets={"target": FileInput(target, file_format="parquet")},
        key="id",
    )

    prepared_table = pq.read_table(job.source.dataset.parquet_files[0])
    assert prepared_table.schema.field("age").type == pa.int64()
    assert job.source.settings.file_format == "parquet"
    assert job.source.settings.encoding is None
    assert job.source.settings.delimiter is None

    result = job.targets["target"].comparison
    assert isinstance(result, KeyedComparison)
    assert result.modified_cells == 1
    assert result.modified_cells_file is not None
    assert pq.read_table(result.modified_cells_file).to_pylist() == [
        {
            "record_key": "001",
            "column_name": "age",
            "source_value": "25",
            "target_value": "26",
            "value_type": "BIGINT",
        }
    ]


def test_invalid_parquet_target_does_not_block_other_targets(tmp_path: Path) -> None:
    source = tmp_path / "source.parquet"
    pq.write_table(pa.table({"id": ["001"]}), source)
    broken = tmp_path / "broken.parquet"
    broken.write_bytes(b"Not a Parquet file.")

    job = ComparisonService(tmp_path / "jobs").run(
        source=FileInput(source, file_format="parquet"),
        targets={
            "broken": FileInput(broken, file_format="parquet"),
            "valid": FileInput(source, file_format="parquet"),
        },
        key="id",
    )

    assert job.targets["broken"].error is not None
    assert job.targets["broken"].comparison is None
    valid = job.targets["valid"].comparison
    assert isinstance(valid, KeyedComparison)
    assert valid.identical is True


def test_mixed_formats_do_not_silently_convert_column_types(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,age\n001,25\n", encoding="utf-8")
    target = tmp_path / "target.parquet"
    pq.write_table(pa.table({"id": ["001"], "age": [25]}), target)

    job = ComparisonService(tmp_path / "jobs").run(
        source=FileInput(source),
        targets={"target": FileInput(target, file_format="parquet")},
        key="id",
    )

    outcome = job.targets["target"]
    assert outcome.error is None
    assert isinstance(outcome.comparison, KeyedComparison)
    assert outcome.comparison.status == "not_compared"
    differences = outcome.comparison.structure.type_differences
    assert len(differences) == 1
    assert differences[0].name == "age"
    assert differences[0].source_type == "VARCHAR"
    assert differences[0].target_type == "BIGINT"
