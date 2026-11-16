import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from src.comparison.duckdb_comparator import DuckDBComparator
from src.exceptions import DatasetValidationError, DbtwinError, IngestionError
from src.ingestion.csv_reader import CsvReader
from src.ingestion.excel_reader import ExcelReader
from src.ingestion.parquet_reader import ParquetReader
from src.models import Dataset, DatasetOrigin, KeyedComparison, UnkeyedComparison
from src.transformation.normalizer import ParquetNormalizer


@dataclass(frozen=True)
class FileInput:
    """Describe a file input and its reading options."""

    path: Path
    encoding: str | None = None
    delimiter: str | None = None
    file_format: Literal["csv", "parquet", "excel"] = "csv"
    sheet_name: str | None = None


@dataclass(frozen=True)
class FileSettings:
    """Describe the effective settings used during ingestion."""

    file_format: Literal["csv", "parquet", "excel"]
    encoding: str | None = None
    delimiter: str | None = None
    encoding_detected: bool = False
    delimiter_detected: bool = False
    sheet_name: str | None = None


@dataclass(frozen=True)
class PreparedFile:
    """Keep a prepared dataset and its effective ingestion settings."""

    dataset: Dataset
    settings: FileSettings


# Preserve compatibility with existing CSV callers.
CsvInput = FileInput
PreparedCsv = PreparedFile


@dataclass(frozen=True)
class TargetOutcome:
    """Describe either a target comparison or an operational failure."""

    prepared: PreparedFile | None = None
    comparison: KeyedComparison | UnkeyedComparison | None = None
    error: str | None = None


@dataclass(frozen=True)
class ComparisonJob:
    """Collect the source, target outcomes and job workspace."""

    job_id: str
    workspace: Path
    source: PreparedFile
    key: str | None
    targets: dict[str, TargetOutcome]


class ComparisonService:
    """Orchestrate file ingestion and independent target comparisons."""

    def __init__(self, jobs_dir: Path) -> None:
        self.jobs_dir = jobs_dir.resolve()
        self.reader = CsvReader()
        self.parquet_reader = ParquetReader()
        self.excel_reader = ExcelReader()
        self.normalizer = ParquetNormalizer()
        self.comparator = DuckDBComparator()

    def run(
        self, source: FileInput, targets: dict[str, FileInput], key: str | None = None
    ) -> ComparisonJob:
        """Prepare a source and compare each target independently."""
        if not targets:
            raise DatasetValidationError("At least one target is required.")
        if any(not name.strip() for name in targets):
            raise DatasetValidationError("Target names must not be empty.")
        if key is not None and not key.strip():
            raise DatasetValidationError(
                "Use None for an unkeyed comparison or provide a valid key name."
            )

        job_id = uuid4().hex
        workspace = self.jobs_dir / job_id
        try:
            workspace.mkdir(parents=True, exist_ok=False)
        except OSError as error:
            raise DbtwinError("Unable to create the comparison workspace.") from error

        try:
            prepared_source = self._prepare(source, source.path.name, workspace / "normalized")
            if key is not None:
                self.comparator.validate_key(prepared_source.dataset, key)
        except Exception:
            # Remove only the workspace belonging to this failed job.
            shutil.rmtree(workspace)
            raise

        comparison: KeyedComparison | UnkeyedComparison
        outcomes: dict[str, TargetOutcome] = {}

        for name, request in targets.items():
            prepared_target: PreparedFile | None = None
            try:
                prepared_target = self._prepare(request, name, workspace / "normalized")
                output_dir = workspace / "differences"

                if key is None:
                    comparison = self.comparator.compare_without_key(
                        prepared_source.dataset,
                        prepared_target.dataset,
                        output_dir=output_dir,
                    )
                else:
                    comparison = self.comparator.compare_with_key(
                        prepared_source.dataset,
                        prepared_target.dataset,
                        key=key,
                        output_dir=output_dir,
                    )

                outcomes[name] = TargetOutcome(prepared=prepared_target, comparison=comparison)
            except DbtwinError as error:
                outcomes[name] = TargetOutcome(
                    prepared=prepared_target,
                    error=str(error),
                )

        return ComparisonJob(
            job_id=job_id,
            workspace=workspace,
            source=prepared_source,
            key=key,
            targets=outcomes,
        )

    def _prepare(self, request: FileInput, name: str, output_dir: Path) -> PreparedFile:
        """Select a reader and prepare the shared internal representation."""
        if request.file_format != "csv":
            if request.encoding is not None or request.delimiter is not None:
                raise IngestionError("Encoding and delimiter settings apply only to CSV files.")
        if request.file_format != "excel" and request.sheet_name is not None:
            raise IngestionError("Worksheet selection applies only to Excel files.")

        if request.file_format == "csv":
            csv_settings = self.reader.inspect(
                request.path,
                encoding=request.encoding,
                delimiter=request.delimiter,
            )
            settings = FileSettings(
                file_format="csv",
                encoding=csv_settings.encoding,
                delimiter=csv_settings.delimiter,
                encoding_detected=csv_settings.encoding_detected,
                delimiter_detected=csv_settings.delimiter_detected,
            )
            batches = self.reader.read_batches(request.path, csv_settings)

        elif request.file_format == "parquet":
            settings = FileSettings(file_format="parquet")
            batches = self.parquet_reader.read_batches(request.path)

        elif request.file_format == "excel":
            sheets = self.excel_reader.list_sheets(request.path)
            if not sheets:
                raise IngestionError("The workbook contains no worksheets.")

            selected_sheet = sheets[0] if request.sheet_name is None else request.sheet_name
            if selected_sheet not in sheets:
                raise IngestionError(f"Worksheet '{selected_sheet}' does not exist.")

            settings = FileSettings(
                file_format="excel",
                sheet_name=selected_sheet,
            )
            batches = self.excel_reader.read_batches(
                request.path,
                sheet_name=selected_sheet,
            )

        else:
            raise IngestionError(f"Unsupported file format: '{request.file_format}'.")

        dataset = self.normalizer.normalize(
            batches=batches,
            output_dir=output_dir,
            name=name,
            origin=DatasetOrigin(
                ingestion_mode="file",
                location=str(request.path.resolve()),
                file_format=request.file_format,
                sheet_name=settings.sheet_name,
            ),
        )
        return PreparedFile(dataset=dataset, settings=settings)
