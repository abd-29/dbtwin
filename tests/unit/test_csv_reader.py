from pathlib import Path

import pytest

from src.exceptions import DatasetValidationError, FormatDetectionError, IngestionError
from src.ingestion.csv_reader import CsvReader


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
def test_detects_supported_delimiters(tmp_path: Path, delimiter: str) -> None:
    rows = [
        ["id", "name", "city"],
        ["001", "Alice", "Rennes"],
        ["002", "Bob", "Vannes"],
    ]
    content = "\n".join(delimiter.join(row) for row in rows)
    path = tmp_path / "input.csv"
    path.write_text(content, encoding="utf-8")

    settings = CsvReader().inspect(path)

    assert settings.delimiter == delimiter
    assert settings.encoding == "utf-8"
    assert settings.delimiter_detected is True
    assert settings.encoding_detected is True


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32"])
def test_detects_unicode_signatures(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "input.csv"
    path.write_text("id;name\n001;Élodie\n002;Moussa\n", encoding=encoding)

    settings = CsvReader().inspect(path)

    assert settings.encoding == encoding
    assert settings.delimiter == ";"


def test_accepts_explicit_settings(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_bytes("id;name\n001;Élodie\n".encode("cp1252"))

    settings = CsvReader().inspect(path, encoding="cp1252", delimiter=";")

    assert settings.encoding == "cp1252"
    assert settings.delimiter == ";"
    assert settings.encoding_detected is False
    assert settings.delimiter_detected is False


def test_handles_quoted_delimiters(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(
        'id;description\n001;"Paris, France"\n002;"Rennes, France"\n',
        encoding="utf-8",
    )

    settings = CsvReader().inspect(path)

    assert settings.delimiter == ";"


@pytest.mark.parametrize("content", [b"", b"   \n\t\n"])
def test_rejects_empty_content(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "input.csv"
    path.write_bytes(content)

    with pytest.raises(IngestionError):
        CsvReader().inspect(path)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(IngestionError, match="Unable to open"):
        CsvReader().inspect(tmp_path / "missing.csv")


def test_requests_encoding_for_non_utf8_content(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_bytes("id;name\n001;Élodie\n".encode("cp1252"))

    with pytest.raises(FormatDetectionError, match="encoding"):
        CsvReader().inspect(path)


def test_rejects_incorrect_explicit_encoding(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_bytes("id;name\n001;Élodie\n".encode("cp1252"))

    with pytest.raises(IngestionError, match="decode"):
        CsvReader().inspect(path, encoding="utf-8", delimiter=";")


@pytest.mark.parametrize("delimiter", ["", "::", "\n", "\r", '"', "\0"])
def test_rejects_invalid_delimiters(tmp_path: Path, delimiter: str) -> None:
    path = tmp_path / "input.csv"
    path.write_text("id,name\n001,Alice\n", encoding="utf-8")

    with pytest.raises(IngestionError, match="delimiter"):
        CsvReader().inspect(path, delimiter=delimiter)


def test_accepts_single_column_with_explicit_delimiter(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text("name\nAlice\nBob\n", encoding="utf-8")

    settings = CsvReader().inspect(path, delimiter=",")

    assert settings.delimiter == ","
    assert settings.delimiter_detected is False


def test_inspection_does_not_modify_file(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    original = b"id,name\n001,Alice\n002,Bob\n"
    path.write_bytes(original)

    CsvReader().inspect(path)

    assert path.read_bytes() == original


def test_preserves_text_values(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(
        "id,value\n00123,10.00\n00456,NA\n00789,NULL\n",
        encoding="utf-8",
    )
    reader = CsvReader()
    settings = reader.inspect(path)

    rows = [row for batch in reader.read_batches(path, settings) for row in batch.to_pylist()]

    assert rows == [
        {"id": "00123", "value": "10.00"},
        {"id": "00456", "value": "NA"},
        {"id": "00789", "value": "NULL"},
    ]


def test_preserves_empty_fields_and_spaces(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text('id,value\n001,\n002," Rennes "\n', encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path)

    rows = [row for batch in reader.read_batches(path, settings) for row in batch.to_pylist()]

    assert rows == [
        {"id": "001", "value": ""},
        {"id": "002", "value": " Rennes "},
    ]


def test_reads_quoted_multiline_values(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(
        'id;description\n001;"Hello; world\nSecond line"\n',
        encoding="utf-8",
        newline="",
    )
    reader = CsvReader()
    settings = reader.inspect(path, delimiter=";")

    rows = [row for batch in reader.read_batches(path, settings) for row in batch.to_pylist()]

    assert rows == [{"id": "001", "description": "Hello; world\nSecond line"}]


def test_reads_multiple_batches_without_losing_records(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    expected = [{"id": f"{index:05d}", "name": f"Customer {index}"} for index in range(100)]
    content = "id,name\n" + "".join(f"{row['id']},{row['name']}\n" for row in expected)
    path.write_text(content, encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path)

    batches = list(reader.read_batches(path, settings, block_size=256))
    rows = [row for batch in batches for row in batch.to_pylist()]

    assert len(batches) > 1
    assert rows == expected


def test_reads_header_only_csv(tmp_path: Path) -> None:
    path = tmp_path / "input.csv"
    path.write_text("id,name\n", encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path, delimiter=",")

    batches = list(reader.read_batches(path, settings))

    assert batches
    assert sum(batch.num_rows for batch in batches) == 0
    assert batches[0].schema.names == ["id", "name"]


@pytest.mark.parametrize("header", ["id,id", "id,", "id,   "])
def test_rejects_invalid_column_names(tmp_path: Path, header: str) -> None:
    path = tmp_path / "input.csv"
    path.write_text(f"{header}\n001,Alice\n", encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path, delimiter=",")

    with pytest.raises(DatasetValidationError):
        list(reader.read_batches(path, settings))


@pytest.mark.parametrize("record", ["002", "002,Bob,Paris"])
def test_rejects_inconsistent_record_width(tmp_path: Path, record: str) -> None:
    path = tmp_path / "input.csv"
    path.write_text(f"id,name\n001,Alice\n{record}\n", encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path, delimiter=",")

    with pytest.raises(IngestionError, match="Unable to read"):
        list(reader.read_batches(path, settings))


@pytest.mark.parametrize("block_size", [0, -1])
def test_rejects_invalid_block_size(tmp_path: Path, block_size: int) -> None:
    path = tmp_path / "input.csv"
    path.write_text("id,name\n001,Alice\n", encoding="utf-8")
    reader = CsvReader()
    settings = reader.inspect(path)

    with pytest.raises(IngestionError, match="Block size"):
        list(reader.read_batches(path, settings, block_size=block_size))


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32", "cp1252"])
def test_reads_explicit_encodings(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "input.csv"
    path.write_text("id;name\n001;Élodie\n", encoding=encoding)
    reader = CsvReader()
    settings = reader.inspect(path, encoding=encoding, delimiter=";")

    rows = [row for batch in reader.read_batches(path, settings) for row in batch.to_pylist()]

    assert rows == [{"id": "001", "name": "Élodie"}]
