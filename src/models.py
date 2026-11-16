from dataclasses import dataclass, field
from pathlib import Path
from src.exceptions import DatasetValidationError


@dataclass(frozen=True)
class ColumnSchema:
    """Describe a column before and after ingestion."""

    name: str
    source_type: str
    stored_type: str


@dataclass(frozen=True)
class DatasetOrigin:
    """Describe the origin of an ingested dataset."""

    ingestion_mode: str
    location: str
    file_format: str | None = None


@dataclass(frozen=True)
class Dataset:
    """Reference a prepared dataset without loading its contents."""

    dataset_id: str
    name: str
    origin: DatasetOrigin
    parquet_files: tuple[Path, ...]
    columns: tuple[ColumnSchema, ...]
    row_count: int
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate the internal dataset contract."""
        if not self.dataset_id.strip():
            raise DatasetValidationError("Dataset identifier must not be empty.")
        if not self.name.strip():
            raise DatasetValidationError("Dataset name must not be empty.")
        if self.row_count < 0:
            raise DatasetValidationError("Row count must not be negative.")
        if not self.parquet_files:
            raise DatasetValidationError("At least one Parquet file must be provided.")
        if not self.columns:
            raise DatasetValidationError("At least one column must be provided.")

        names = self.column_names
        if any(not name.strip() for name in names):
            raise DatasetValidationError("Column names must not be empty.")
        if len(names) != len(set(names)):
            raise DatasetValidationError("Column names must be unique.")

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return column names in their stored order."""
        return tuple(column.name for column in self.columns)

    @property
    def column_count(self) -> int:
        """Return the number of columns."""
        return len(self.columns)

@dataclass(frozen=True)
class DatasetSummary:
    """Describe the dimensions of a dataset."""

    name: str
    row_count: int
    column_count: int


@dataclass(frozen=True)
class ColumnTypeDifference:
    """Describe a type difference between matching columns."""

    name: str
    source_type: str
    target_type: str


@dataclass(frozen=True)
class StructureComparison:
    """Describe structural differences without comparing record values."""

    source: DatasetSummary
    target: DatasetSummary
    common_columns: tuple[str, ...]
    missing_columns: tuple[str, ...]
    extra_columns: tuple[str, ...]
    type_differences: tuple[ColumnTypeDifference, ...]

    @property
    def same_schema(self) -> bool:
        """Check whether column names and types match, regardless of order."""
        return not (
            self.missing_columns or self.extra_columns or self.type_differences
        )

    @property
    def same_dimensions(self) -> bool:
        """Check whether row and column counts match."""
        return (
            self.source.row_count == self.target.row_count
            and self.source.column_count == self.target.column_count
        )