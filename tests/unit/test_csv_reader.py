from pathlib import Path

import pytest

from src.exceptions import FormatDetectionError, IngestionError
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