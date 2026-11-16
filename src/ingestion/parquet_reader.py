from collections.abc import Generator
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.exceptions import DatasetValidationError, IngestionError


class ParquetReader:
    """Read Parquet files incrementally while preserving stored types."""

    def read_batches(
        self,
        path: Path,
        batch_size: int = 65_536,
    ) -> Generator[pa.RecordBatch, None, None]:
        """Yield typed batches, including a schema-only batch for empty files."""
        if batch_size <= 0:
            raise IngestionError("Batch size must be greater than zero.")

        try:
            with pq.ParquetFile(str(path)) as file:
                schema = file.schema_arrow
                names = schema.names
                if not names:
                    raise DatasetValidationError("At least one column is required.")
                if any(not name.strip() for name in names):
                    raise DatasetValidationError("Column names must not be empty.")
                if len(names) != len(set(names)):
                    raise DatasetValidationError("Column names must be unique.")

                has_batches = False
                for batch in file.iter_batches(batch_size=batch_size):
                    has_batches = True
                    yield batch

                if not has_batches:
                    yield pa.RecordBatch.from_arrays(
                        [pa.array([], type=field.type) for field in schema],
                        schema=schema,
                    )
        except (pa.ArrowException, OSError) as error:
            raise IngestionError(f"Unable to read Parquet file '{path.name}'.") from error
