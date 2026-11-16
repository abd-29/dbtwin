import json
from collections.abc import Iterator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.exceptions import DatasetValidationError, IngestionError
from src.models import DatasetOrigin
from src.transformation.normalizer import ParquetNormalizer

ORIGIN = DatasetOrigin(
    ingestion_mode="file",
    location="synthetic.csv",
    file_format="csv",
)


def test_preserves_values_and_metadata(tmp_path: Path) -> None:
    batches = [
        pa.record_batch({"id": ["001", "002"], "value": ["", "NA"]}),
        pa.record_batch({"id": ["003"], "value": [" Rennes "]}),
    ]

    dataset = ParquetNormalizer().normalize(batches, tmp_path, "customers", ORIGIN)
    table = pq.read_table(dataset.parquet_files[0])
    metadata_path = dataset.parquet_files[0].parent / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert table.to_pylist() == [
        {"id": "001", "value": ""},
        {"id": "002", "value": "NA"},
        {"id": "003", "value": " Rennes "},
    ]
    assert dataset.row_count == 3
    assert dataset.column_names == ("id", "value")
    assert dataset.column_count == 2
    assert dataset.origin == ORIGIN
    assert metadata["dataset_id"] == dataset.dataset_id
    assert metadata["row_count"] == 3
    assert metadata["parquet_files"] == [str(dataset.parquet_files[0])]
    assert {path.name for path in tmp_path.iterdir()} == {dataset.dataset_id}


def test_preserves_empty_dataset_schema(tmp_path: Path) -> None:
    batch = pa.record_batch(
        {
            "id": pa.array([], type=pa.string()),
            "name": pa.array([], type=pa.string()),
        }
    )

    dataset = ParquetNormalizer().normalize([batch], tmp_path, "empty", ORIGIN)
    table = pq.read_table(dataset.parquet_files[0])

    assert dataset.row_count == 0
    assert table.num_rows == 0
    assert table.schema.equals(batch.schema)


def test_rejects_missing_batches(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="at least one batch"):
        ParquetNormalizer().normalize([], tmp_path, "empty", ORIGIN)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "second_batch",
    [
        pa.record_batch({"other_id": ["002"]}),
        pa.record_batch({"id": [2]}),
    ],
    ids=["different-column", "different-type"],
)
def test_rejects_schema_changes(tmp_path: Path, second_batch: pa.RecordBatch) -> None:
    first_batch = pa.record_batch({"id": ["001"]})

    with pytest.raises(DatasetValidationError, match="same column names and types"):
        ParquetNormalizer().normalize([first_batch, second_batch], tmp_path, "invalid", ORIGIN)

    assert list(tmp_path.iterdir()) == []


def test_removes_partial_output_after_reading_failure(tmp_path: Path) -> None:
    existing = tmp_path / "existing.txt"
    existing.write_text("Keep this file.", encoding="utf-8")

    def failing_batches() -> Iterator[pa.RecordBatch]:
        yield pa.record_batch({"id": ["001"]})
        raise IngestionError("Malformed CSV record.")

    with pytest.raises(IngestionError, match="Malformed CSV"):
        ParquetNormalizer().normalize(failing_batches(), tmp_path, "broken", ORIGIN)

    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "Keep this file."


def test_creates_independent_datasets(tmp_path: Path) -> None:
    normalizer = ParquetNormalizer()
    first = normalizer.normalize([pa.record_batch({"id": ["001"]})], tmp_path, "customers", ORIGIN)
    second = normalizer.normalize([pa.record_batch({"id": ["002"]})], tmp_path, "customers", ORIGIN)

    assert first.dataset_id != second.dataset_id
    assert pq.read_table(first.parquet_files[0]).to_pylist() == [{"id": "001"}]
    assert pq.read_table(second.parquet_files[0]).to_pylist() == [{"id": "002"}]


@pytest.mark.parametrize("names", [["id", "id"], ["id", ""], ["id", "   "]])
def test_rejects_invalid_column_names(tmp_path: Path, names: list[str]) -> None:
    batch = pa.RecordBatch.from_arrays([pa.array(["001"]), pa.array(["Alice"])], names=names)

    with pytest.raises(DatasetValidationError):
        ParquetNormalizer().normalize([batch], tmp_path, "invalid", ORIGIN)

    assert list(tmp_path.iterdir()) == []
