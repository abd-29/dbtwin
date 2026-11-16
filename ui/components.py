from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from shutil import copyfileobj
from typing import Any, Literal
from uuid import uuid4

import duckdb
import pyarrow as pa
import streamlit as st
from streamlit.runtime.uploaded_file_manager import UploadedFile

from config import FILETYPE, INGESTION_MODES
from src.exceptions import IngestionError
from src.ingestion.csv_reader import CsvReader
from src.ingestion.parquet_reader import ParquetReader
from src.services.comparison_service import FileInput, FileSettings

PREVIEW_ROWS = 100

SEPARATORS = {
    "Auto": None,
    "Comma": ",",
    "Semicolon": ";",
    "Tab": "\t",
    "Pipe": "|",
}

ENCODINGS = {
    "Auto": None,
    "UTF-8": "utf-8",
    "UTF-8 with BOM": "utf-8-sig",
    "UTF-16": "utf-16",
    "UTF-32": "utf-32",
    "Windows-1252": "cp1252",
    "Latin-1": "latin-1",
}


def reading_options(prefix: str) -> tuple[str | None, str | None]:
    """Collect optional CSV reading overrides."""
    with st.expander("Reading settings"):
        separator = st.selectbox("Separator", list(SEPARATORS), key=f"{prefix}_separator")
        encoding = st.selectbox("Encoding", list(ENCODINGS), key=f"{prefix}_encoding")
    return ENCODINGS[encoding], SEPARATORS[separator]


def ingestion_controls(
    prefix: str, multiple: bool
) -> (
    tuple[
        list[UploadedFile],
        str | None,
        str | None,
        Literal["csv", "parquet"],
    ]
    | None
):
    """Collect file uploads and format-specific reading options."""
    mode = st.selectbox(
        "Connection",
        options=list(INGESTION_MODES),
        format_func=lambda value: (
            INGESTION_MODES[value]["label"]
            + ("" if INGESTION_MODES[value]["enabled"] else " · Coming soon")
        ),
        key=f"{prefix}_connection",
    )
    if mode is None:
        return None
    if not INGESTION_MODES[mode]["enabled"]:
        st.info(f"{INGESTION_MODES[mode]['label']} is not available yet.")
        return None

    filetype = st.selectbox(
        "File format",
        options=list(FILETYPE),
        format_func=lambda value: (
            FILETYPE[value]["label"] + ("" if FILETYPE[value]["enabled"] else " · Coming soon")
        ),
        key=f"{prefix}_format",
    )
    if filetype is None:
        return None
    if not FILETYPE[filetype]["enabled"]:
        st.info(f"{FILETYPE[filetype]['label']} is not available yet.")
        return None
    if mode != "file" or filetype not in ("csv", "parquet"):
        st.error("No reader is configured for this selection.")
        return None

    file_format: Literal["csv", "parquet"] = "csv" if filetype == "csv" else "parquet"
    uploads = st.file_uploader(
        "Target files" if multiple else "Source file",
        type=FILETYPE[filetype]["extensions"],
        accept_multiple_files=multiple,
        key=f"{prefix}_files_{file_format}",
        label_visibility="collapsed",
    )

    files: list[UploadedFile]
    if uploads is None:
        files = []
    elif isinstance(uploads, UploadedFile):
        files = [uploads]
    else:
        files = uploads

    encoding, delimiter = reading_options(prefix) if file_format == "csv" else (None, None)
    return files, encoding, delimiter, file_format


def save_upload(
    upload: UploadedFile, directory: Path, file_format: Literal["csv", "parquet"] = "csv"
) -> Path:
    """Save an upload using its selected format."""
    path = directory / f"{uuid4().hex}.{file_format}"
    upload.seek(0)
    with path.open("wb") as destination:
        copyfileobj(upload, destination)
    return path


def file_preview(request: FileInput) -> tuple[pa.RecordBatch, FileSettings]:
    """Preview CSV or Parquet without loading the complete dataset."""
    if request.file_format == "csv":
        reader = CsvReader()
        detected = reader.inspect(
            request.path,
            encoding=request.encoding,
            delimiter=request.delimiter,
        )
        settings = FileSettings(
            file_format="csv",
            encoding=detected.encoding,
            delimiter=detected.delimiter,
            encoding_detected=detected.encoding_detected,
            delimiter_detected=detected.delimiter_detected,
        )
        batches = reader.read_batches(request.path, detected)
    elif request.file_format == "parquet":
        if request.encoding is not None or request.delimiter is not None:
            raise IngestionError("Encoding and delimiter settings apply only to CSV files.")
        settings = FileSettings(file_format="parquet")
        batches = ParquetReader().read_batches(request.path)
    else:
        raise IngestionError(f"Unsupported file format: '{request.file_format}'.")

    with closing(batches):
        preview = next(batches).slice(0, PREVIEW_ROWS)
    return preview, settings


def parquet_preview(paths: tuple[Path, ...]) -> list[tuple[Any, ...]]:
    """Fetch only the records needed for a preview."""
    with duckdb.connect(database=":memory:") as connection:
        return (
            connection.read_parquet(
                [str(path) for path in paths],
                hive_partitioning=False,
            )
            .limit(PREVIEW_ROWS)
            .fetchall()
        )


def directory_key(inputs: Mapping[str, Any]) -> str:
    """Identify widgets associated with the loaded file selection."""
    source: FileInput = inputs["source"]
    return source.path.parent.name


def difference_table(path: Path | None, widget_key: str) -> None:
    """Display a result preview and provide full-file downloads."""
    if path is None:
        st.info("Not available.")
        return

    with duckdb.connect(database=":memory:") as connection:
        relation = connection.read_parquet(str(path), hive_partitioning=False)
        rows = relation.limit(PREVIEW_ROWS).fetchall()
        names = relation.columns

        if rows:
            st.dataframe(
                [dict(zip(names, row, strict=True)) for row in rows],
                hide_index=True,
                use_container_width=True,
            )
            st.caption(f"First {PREVIEW_ROWS} rows at most.")
        else:
            st.info("No differences.")

        if st.button("Prepare CSV", key=f"prepare_{widget_key}"):
            relation.to_csv(
                str(path.with_suffix(".csv")),
                header=True,
                na_rep="\\N",
            )

    csv_path = path.with_suffix(".csv")
    left, right = st.columns(2)

    with left:
        with path.open("rb") as file:
            st.download_button(
                "Download Parquet",
                data=file,
                file_name=path.name,
                mime="application/octet-stream",
                key=f"parquet_{widget_key}",
            )

    with right:
        if csv_path.exists():
            with csv_path.open("rb") as file:
                st.download_button(
                    "Download CSV",
                    data=file,
                    file_name=csv_path.name,
                    mime="text/csv",
                    key=f"csv_{widget_key}",
                )
            st.caption(r"Null values are written as \N.")
