from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.exceptions import IngestionError
from src.ingestion.parquet_reader import ParquetReader


def test_preserves_types_values_and_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "input.parquet"
    original = pa.table(
        {
            "id": ["001", "001", "002"],
            "amount": pa.array([10, 10, None], type=pa.int64()),
            "active": [True, True, False],
            "description": ["", "", "NA"],
        }
    )
    pq.write_table(original, path)
    original_bytes = path.read_bytes()

    batches = list(ParquetReader().read_batches(path, batch_size=2))
    actual = pa.Table.from_batches(batches)

    assert len(batches) == 2
    assert actual.equals(original)
    assert path.read_bytes() == original_bytes


def test_preserves_empty_dataset_schema(tmp_path: Path) -> None:
    path = tmp_path / "empty.parquet"
    original = pa.table(
        {
            "id": pa.array([], type=pa.string()),
            "amount": pa.array([], type=pa.int64()),
        }
    )
    pq.write_table(original, path)

    batches = list(ParquetReader().read_batches(path))

    assert batches
    assert pa.Table.from_batches(batches).equals(original)


@pytest.mark.parametrize("batch_size", [0, -1])
def test_rejects_invalid_batch_size(tmp_path: Path, batch_size: int) -> None:
    with pytest.raises(IngestionError, match="Batch size"):
        list(ParquetReader().read_batches(tmp_path / "unused.parquet", batch_size=batch_size))


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="Unable to read Parquet"):
        list(ParquetReader().read_batches(tmp_path / "missing.parquet"))


def test_rejects_invalid_file(tmp_path: Path) -> None:
    path = tmp_path / "invalid.parquet"
    path.write_bytes(b"This is not a Parquet file.")

    with pytest.raises(IngestionError, match="Unable to read Parquet"):
        list(ParquetReader().read_batches(path))
