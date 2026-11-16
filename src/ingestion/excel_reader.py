from collections.abc import Generator
from contextlib import closing
from datetime import date, datetime, time
from pathlib import Path
from typing import IO
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile

import pyarrow as pa
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from src.exceptions import DatasetValidationError, IngestionError

EXCEL_READ_ERRORS = (
    OSError,
    BadZipFile,
    InvalidFileException,
    ParseError,
    ValueError,
    KeyError,
)


class ExcelReader:
    """Read XLSX worksheets as textual Arrow batches."""

    def list_sheets(self, source: Path | IO[bytes]) -> tuple[str, ...]:
        """List worksheets from a path or an uploaded binary file."""
        try:
            with closing(
                load_workbook(source, read_only=True, data_only=False, keep_links=False)
            ) as workbook:
                return tuple(sheet.title for sheet in workbook.worksheets)
        except EXCEL_READ_ERRORS as error:
            raise IngestionError("Unable to list Excel worksheets.") from error

    def read_batches(
        self,
        path: Path,
        sheet_name: str | None = None,
        batch_size: int = 10_000,
    ) -> Generator[pa.RecordBatch, None, None]:
        """Read a worksheet using its first row as column names."""
        if batch_size <= 0:
            raise IngestionError("Batch size must be greater than zero.")

        try:
            with closing(
                load_workbook(path, read_only=True, data_only=False, keep_links=False)
            ) as workbook:
                sheets = workbook.worksheets
                if not sheets:
                    raise IngestionError("The workbook contains no worksheets.")

                selected = sheets[0].title if sheet_name is None else sheet_name
                worksheet = next((sheet for sheet in sheets if sheet.title == selected), None)
                if worksheet is None:
                    raise IngestionError(f"Worksheet '{selected}' does not exist.")

                records = worksheet.iter_rows()
                header = next(records, None)
                if header is None:
                    raise DatasetValidationError("The worksheet has no header.")

                names = []
                for cell in header:
                    if cell.data_type in ("f", "e"):
                        raise DatasetValidationError(
                            "Header cells must not contain formulas or Excel errors."
                        )
                    if not isinstance(cell.value, str) or not cell.value.strip():
                        raise DatasetValidationError("Column names must be non-empty text.")
                    names.append(cell.value)

                if not names:
                    raise DatasetValidationError("At least one column is required.")
                if len(names) != len(set(names)):
                    raise DatasetValidationError("Column names must be unique.")

                schema = pa.schema([(name, pa.string()) for name in names])
                buffer: list[list[str | None]] = []
                emitted = False

                for record in records:
                    values = []
                    for cell in record:
                        if cell.data_type == "f":
                            raise IngestionError(
                                f"Formula found in worksheet '{selected}' "
                                f"at {cell.coordinate}. Replace it with a value."
                            )
                        if cell.data_type == "e":
                            raise IngestionError(
                                f"Excel error found in worksheet '{selected}' at {cell.coordinate}."
                            )
                        values.append(self._as_text(cell.value))

                    if len(values) > len(names):
                        raise IngestionError("Record width exceeds the worksheet header.")
                    values.extend([None] * (len(names) - len(values)))
                    buffer.append(values)
                    if len(buffer) >= batch_size:
                        yield self._make_batch(buffer, schema)
                        emitted = True
                        buffer = []

                if buffer or not emitted:
                    yield self._make_batch(buffer, schema)
        except EXCEL_READ_ERRORS as error:
            raise IngestionError(f"Unable to read Excel file '{path.name}'.") from error

    @staticmethod
    def _as_text(value: object) -> str | None:
        """Serialize cell values without applying Excel display formatting."""
        if value is None:
            return None
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    @staticmethod
    def _make_batch(rows: list[list[str | None]], schema: pa.Schema) -> pa.RecordBatch:
        """Build one batch using a stable textual schema."""
        return pa.RecordBatch.from_arrays(
            [
                pa.array([row[index] for row in rows], type=pa.string())
                for index in range(len(schema))
            ],
            schema=schema,
        )
