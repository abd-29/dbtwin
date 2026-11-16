from pathlib import Path

import pyarrow.parquet as pq
import pytest

from src.exceptions import IngestionError
from src.ingestion.csv_reader import CsvReader
from src.models import DatasetOrigin
from src.transformation.normalizer import ParquetNormalizer


def test_csv_to_parquet_preserves_records(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text(
        'id;name;value\n'
        '001;Élodie;\n'
        '002;"Alice; Bob";NA\n'
        '002;"Alice; Bob";NA\n'
        '003;"First line\nSecond line";" Rennes "\n',
        encoding="utf-8-sig",
        newline="",
    )
    original = source.read_bytes()
    reader = CsvReader()
    settings = reader.inspect(source)

    dataset = ParquetNormalizer().normalize(
        batches=reader.read_batches(source, settings),
        output_dir=tmp_path / "normalized",
        name="customers",
        origin=DatasetOrigin(
            ingestion_mode="file",
            location=str(source),
            file_format="csv",
        ),
    )
    table = pq.read_table(dataset.parquet_files[0])

    assert settings.encoding == "utf-8-sig"
    assert settings.delimiter == ";"
    assert table.to_pylist() == [
        {"id": "001", "name": "Élodie", "value": ""},
        {"id": "002", "name": "Alice; Bob", "value": "NA"},
        {"id": "002", "name": "Alice; Bob", "value": "NA"},
        {
            "id": "003",
            "name": "First line\nSecond line",
            "value": " Rennes ",
        },
    ]
    assert dataset.row_count == 4
    assert dataset.column_names == ("id", "name", "value")
    assert all(column.stored_type == "string" for column in dataset.columns)
    assert source.read_bytes() == original
    assert (dataset.parquet_files[0].parent / "metadata.json").is_file()


def test_malformed_csv_does_not_publish_partial_dataset(tmp_path: Path) -> None:
    source = tmp_path / "broken.csv"
    source.write_text(
        "id;name\n"
        + "".join(f"{index:05d};Customer {index}\n" for index in range(100))
        + "99999;Unexpected;Extra field\n",
        encoding="utf-8",
    )
    output_dir = tmp_path / "normalized"
    reader = CsvReader()
    settings = reader.inspect(source, delimiter=";")

    with pytest.raises(IngestionError):
        ParquetNormalizer().normalize(
            batches=reader.read_batches(source, settings, block_size=256),
            output_dir=output_dir,
            name="broken",
            origin=DatasetOrigin(
                ingestion_mode="file",
                location=str(source),
                file_format="csv",
            ),
        )

    assert source.is_file()
    assert not output_dir.exists() or list(output_dir.iterdir()) == []