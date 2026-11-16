import json
from collections.abc import Iterable
from dataclasses import asdict
from itertools import chain
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pyarrow as pa
import pyarrow.parquet as pq

from src.exceptions import DatasetValidationError, TransformationError
from src.models import ColumnSchema, Dataset, DatasetOrigin


class ParquetNormalizer:
    """Store Arrow batches as a validated internal Parquet dataset."""

    def normalize(
        self,
        batches: Iterable[pa.RecordBatch],
        output_dir: Path,
        name: str,
        origin: DatasetOrigin,
    ) -> Dataset:
        """Prepare a dataset without collecting all batches in memory."""
        if not name.strip():
            raise DatasetValidationError("Dataset name must not be empty.")

        iterator = iter(batches)
        first_batch = next(iterator, None)
        if first_batch is None:
            raise DatasetValidationError(
                "Provide at least one batch, including for an empty dataset."
            )

        schema = first_batch.schema
        self._validate_schema(schema)
        dataset_id = uuid4().hex
        output_dir = output_dir.resolve()
        final_dir = output_dir / dataset_id

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".preparing-", dir=output_dir) as temporary:
                staging_dir = Path(temporary)
                parquet_path = staging_dir / "data.parquet"
                row_count = 0

                with pq.ParquetWriter(
                    str(parquet_path),
                    schema=schema,
                    compression="zstd",
                ) as writer:
                    for batch in chain((first_batch,), iterator):
                        if not batch.schema.equals(schema, check_metadata=False):
                            raise DatasetValidationError(
                                "All batches must have the same column names and types."
                            )
                        writer.write_batch(batch)
                        row_count += batch.num_rows

                dataset = Dataset(
                    dataset_id=dataset_id,
                    name=name,
                    origin=origin,
                    parquet_files=(final_dir / "data.parquet",),
                    columns=tuple(
                        ColumnSchema(
                            name=column.name,
                            source_type=str(column.type),
                            stored_type=str(column.type),
                        )
                        for column in schema
                    ),
                    row_count=row_count,
                )
                metadata = json.dumps(
                    asdict(dataset), default=str, ensure_ascii=False, indent=2
                )
                (staging_dir / "metadata.json").write_text(
                    metadata + "\n", encoding="utf-8"
                )

                # Publish only after the writer and metadata file are closed.
                staging_dir.rename(final_dir)
                return dataset
        except (OSError, pa.ArrowException) as error:
            raise TransformationError(
                f"Unable to prepare dataset '{name}'."
            ) from error

    @staticmethod
    def _validate_schema(schema: pa.Schema) -> None:
        """Reject missing or ambiguous column names."""
        if not schema.names:
            raise DatasetValidationError("At least one column is required.")
        if any(not name.strip() for name in schema.names):
            raise DatasetValidationError("Column names must not be empty.")
        if len(schema.names) != len(set(schema.names)):
            raise DatasetValidationError("Column names must be unique.")