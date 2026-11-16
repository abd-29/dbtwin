import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from src.comparison.duckdb_comparator import DuckDBComparator
from src.exceptions import DatasetValidationError, DbtwinError
from src.ingestion.csv_reader import CsvReader, CsvSettings
from src.models import Dataset, DatasetOrigin, KeyedComparison, UnkeyedComparison
from src.transformation.normalizer import ParquetNormalizer


@dataclass(frozen=True)
class CsvInput:
    """Describe a CSV input and optional reading overrides."""

    path: Path
    encoding: str | None = None
    delimiter: str | None = None


@dataclass(frozen=True)
class PreparedCsv:
    """Keep the prepared dataset and its effective reading settings."""

    dataset: Dataset
    settings: CsvSettings


@dataclass(frozen=True)
class TargetOutcome:
    """Describe either a target comparison or an operational failure."""

    prepared: PreparedCsv | None = None
    comparison: KeyedComparison | UnkeyedComparison | None = None
    error: str | None = None


@dataclass(frozen=True)
class ComparisonJob:
    """Collect the source, target outcomes and job workspace."""

    job_id: str
    workspace: Path
    source: PreparedCsv
    key: str | None
    targets: dict[str, TargetOutcome]


class ComparisonService:
    """Orchestrate CSV preparation and independent target comparisons."""

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir.resolve()
        self.reader = CsvReader()
        self.normalizer = ParquetNormalizer()
        self.comparator = DuckDBComparator()

    def run(
        self,
        source: CsvInput,
        targets: dict[str, CsvInput],
        key: str | None = None,
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
            # This workspace belongs exclusively to the failed job.
            shutil.rmtree(workspace)
            raise

        comparison: KeyedComparison | UnkeyedComparison

        outcomes = {}
        for name, request in targets.items():
            prepared_target = None
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
                outcomes[name] = TargetOutcome(
                    prepared=prepared_target,
                    comparison=comparison,
                )
            except DbtwinError as error:
                outcomes[name] = TargetOutcome(prepared=prepared_target, error=str(error))

        return ComparisonJob(
            job_id=job_id,
            workspace=workspace,
            source=prepared_source,
            key=key,
            targets=outcomes,
        )

    def _prepare(self, request: CsvInput, name: str, output_dir: Path) -> PreparedCsv:
        """Inspect a CSV and prepare its internal representation."""
        settings = self.reader.inspect(
            request.path,
            encoding=request.encoding,
            delimiter=request.delimiter,
        )
        dataset = self.normalizer.normalize(
            batches=self.reader.read_batches(request.path, settings),
            output_dir=output_dir,
            name=name,
            origin=DatasetOrigin(
                ingestion_mode="file",
                location=str(request.path.resolve()),
                file_format="csv",
            ),
        )
        return PreparedCsv(dataset=dataset, settings=settings)
