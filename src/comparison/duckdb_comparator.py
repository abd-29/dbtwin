from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import duckdb

from src.exceptions import ComparisonError
from src.models import (
    ColumnTypeDifference,
    Dataset,
    DatasetSummary,
    KeyedComparison,
    StructureComparison,
    UnkeyedComparison,
)


class DuckDBComparator:
    """Compare prepared datasets using an isolated DuckDB connection."""

    def compare_structure(self, source: Dataset, target: Dataset) -> StructureComparison:
        """Compare dimensions, column names and stored column types."""
        try:
            with duckdb.connect(database=":memory:") as connection:
                source_summary, source_types = self._inspect(connection, source)
                target_summary, target_types = self._inspect(connection, target)
        except duckdb.Error as error:
            raise ComparisonError("Unable to inspect the prepared Parquet datasets.") from error

        common_columns = tuple(name for name in source_types if name in target_types)
        missing_columns = tuple(name for name in source_types if name not in target_types)
        extra_columns = tuple(name for name in target_types if name not in source_types)
        type_differences = tuple(
            ColumnTypeDifference(
                name=name,
                source_type=source_types[name],
                target_type=target_types[name],
            )
            for name in common_columns
            if source_types[name] != target_types[name]
        )

        return StructureComparison(
            source=source_summary,
            target=target_summary,
            common_columns=common_columns,
            missing_columns=missing_columns,
            extra_columns=extra_columns,
            type_differences=type_differences,
        )

    @staticmethod
    def _inspect(
        connection: duckdb.DuckDBPyConnection, dataset: Dataset
    ) -> tuple[DatasetSummary, dict[str, str]]:
        """Read actual dimensions and check them against dataset metadata."""
        for path in dataset.parquet_files:
            if not path.is_file():
                raise ComparisonError(f"A Parquet file is missing for dataset '{dataset.name}'.")

        relation = connection.read_parquet(
            [str(path) for path in dataset.parquet_files],
            hive_partitioning=False,
            union_by_name=False,
        )
        if tuple(relation.columns) != dataset.column_names:
            raise ComparisonError(f"Stored columns do not match metadata for '{dataset.name}'.")

        result = relation.aggregate("count(*) AS row_count").fetchall()
        if len(result) != 1:
            raise ComparisonError(f"Unable to count records for '{dataset.name}'.")

        row_count = int(result[0][0])
        if row_count != dataset.row_count:
            raise ComparisonError(f"Stored row count does not match metadata for '{dataset.name}'.")

        column_types = {
            name: str(column_type)
            for name, column_type in zip(relation.columns, relation.types, strict=True)
        }
        summary = DatasetSummary(
            name=dataset.name,
            row_count=row_count,
            column_count=len(column_types),
        )
        return summary, column_types

    def compare_without_key(
        self,
        source: Dataset,
        target: Dataset,
        output_dir: Path | None = None,
    ) -> UnkeyedComparison:
        """Compare complete rows and optionally write differences to Parquet."""
        structure = self.compare_structure(source, target)
        if not structure.same_schema:
            return UnkeyedComparison(
                structure=structure,
                status="not_compared",
                reason="Comparison without a key requires matching column names and types.",
            )

        projection = ", ".join(self._quote_identifier(name) for name in structure.common_columns)
        missing_file = None
        added_file = None

        try:
            with duckdb.connect(database=":memory:") as connection:
                connection.read_parquet(
                    [str(path) for path in source.parquet_files],
                    hive_partitioning=False,
                    union_by_name=False,
                ).project(projection).create_view("comparison_source")

                connection.read_parquet(
                    [str(path) for path in target.parquet_files],
                    hive_partitioning=False,
                    union_by_name=False,
                ).project(projection).create_view("comparison_target")

                connection.sql("""
                    SELECT * FROM comparison_source
                    EXCEPT ALL
                    SELECT * FROM comparison_target
                """).create_view("missing_records")

                connection.sql("""
                    SELECT * FROM comparison_target
                    EXCEPT ALL
                    SELECT * FROM comparison_source
                """).create_view("added_records")

                counts = connection.sql("""
                    SELECT
                        (SELECT count(*) FROM missing_records),
                        (SELECT count(*) FROM added_records)
                """).fetchall()
                missing_rows = int(counts[0][0])
                added_rows = int(counts[0][1])
                matched_rows = structure.source.row_count - missing_rows

                if matched_rows != structure.target.row_count - added_rows:
                    raise ComparisonError(
                        "Comparison counts are inconsistent. Check whether datasets changed."
                    )

                if output_dir is not None:
                    missing_file, added_file = self._write_unkeyed_differences(
                        connection, output_dir
                    )
        except (duckdb.Error, OSError) as error:
            raise ComparisonError(
                "Unable to compare or write differences without a key."
            ) from error

        return UnkeyedComparison(
            structure=structure,
            status="complete",
            matched_rows=matched_rows,
            missing_rows=missing_rows,
            added_rows=added_rows,
            missing_rows_file=missing_file,
            added_rows_file=added_file,
        )

    @staticmethod
    def _quote_identifier(name: str) -> str:
        """Escape a column name for use as a SQL identifier."""
        return '"' + name.replace('"', '""') + '"'

    @staticmethod
    def _write_unkeyed_differences(
        connection: duckdb.DuckDBPyConnection, output_dir: Path
    ) -> tuple[Path, Path]:
        """Publish both difference files only after successful writing."""
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        final_dir = output_dir / uuid4().hex

        with TemporaryDirectory(prefix=".preparing-", dir=output_dir) as temporary:
            staging_dir = Path(temporary)
            connection.sql("SELECT * FROM missing_records").to_parquet(
                str(staging_dir / "missing.parquet"),
                compression="zstd",
            )
            connection.sql("SELECT * FROM added_records").to_parquet(
                str(staging_dir / "added.parquet"),
                compression="zstd",
            )
            staging_dir.rename(final_dir)

        return final_dir / "missing.parquet", final_dir / "added.parquet"

    def validate_key(self, dataset: Dataset, key: str) -> None:
        """Validate a record identifier without loading records into Python."""
        if not key.strip():
            raise ComparisonError("The comparison key must not be empty.")
        if key not in dataset.column_names:
            raise ComparisonError(f"Column '{key}' is missing from dataset '{dataset.name}'.")

        identifier = self._quote_identifier(key)
        try:
            with duckdb.connect(database=":memory:") as connection:
                self._inspect(connection, dataset)
                connection.read_parquet(
                    [str(path) for path in dataset.parquet_files],
                    hive_partitioning=False,
                    union_by_name=False,
                ).create_view("key_validation_records")

                counts = connection.sql(f"""
                    SELECT
                        (
                            SELECT count(*)
                            FROM key_validation_records
                            WHERE {identifier} IS NULL
                               OR trim(CAST({identifier} AS VARCHAR)) = ''
                        ) AS empty_keys,
                        (
                            SELECT coalesce(sum(occurrences), 0)
                            FROM (
                                SELECT count(*) AS occurrences
                                FROM key_validation_records
                                GROUP BY {identifier}
                                HAVING count(*) > 1
                            ) AS duplicates
                        ) AS duplicate_rows
                """).fetchall()
        except duckdb.Error as error:
            raise ComparisonError(
                f"Unable to validate the key for dataset '{dataset.name}'."
            ) from error

        empty_keys = int(counts[0][0])
        duplicate_rows = int(counts[0][1])
        problems = []
        if empty_keys:
            problems.append(f"{empty_keys} row(s) without an identifier")
        if duplicate_rows:
            problems.append(f"{duplicate_rows} row(s) with duplicate identifiers")
        if problems:
            raise ComparisonError(
                f"Invalid key '{key}' in dataset '{dataset.name}': " + "; ".join(problems) + "."
            )

    def compare_with_key(
        self, source: Dataset, target: Dataset, key: str, output_dir: Path | None = None
    ) -> KeyedComparison:
        """Compare records using a validated unique identifier."""
        self.validate_key(source, key)
        self.validate_key(target, key)
        structure = self.compare_structure(source, target)
        columns = tuple(name for name in structure.common_columns if name != key)

        if structure.type_differences:
            return KeyedComparison(
                structure=structure,
                key=key,
                status="not_compared",
                compared_columns=(),
                reason="Matching columns must have identical types before comparison.",
            )

        identifier = self._quote_identifier(key)
        differences = [
            f"s.{self._quote_identifier(name)} IS DISTINCT FROM t.{self._quote_identifier(name)}"
            for name in columns
        ]
        row_changed = " OR ".join(f"({expression})" for expression in differences)
        row_changed = row_changed or "FALSE"
        cell_count = " + ".join(
            f"CASE WHEN {expression} THEN 1 ELSE 0 END" for expression in differences
        )
        cell_count = cell_count or "0"

        try:
            with duckdb.connect(database=":memory:") as connection:
                connection.read_parquet(
                    [str(path) for path in source.parquet_files],
                    hive_partitioning=False,
                    union_by_name=False,
                ).create_view("keyed_source")

                connection.read_parquet(
                    [str(path) for path in target.parquet_files],
                    hive_partitioning=False,
                    union_by_name=False,
                ).create_view("keyed_target")

                counts = connection.sql(f"""
                    SELECT
                        count(*) AS matched_rows,
                        coalesce(sum(
                            CASE WHEN {row_changed} THEN 1 ELSE 0 END
                        ), 0) AS modified_rows,
                        coalesce(sum({cell_count}), 0) AS modified_cells
                    FROM keyed_source AS s
                    INNER JOIN keyed_target AS t
                        ON s.{identifier} = t.{identifier}
                """).fetchall()
        except duckdb.Error as error:
            raise ComparisonError(f"Unable to compare records using key '{key}'.") from error

        matched_rows = int(counts[0][0])
        modified_rows = int(counts[0][1])
        modified_cells = int(counts[0][2])
        missing_rows = structure.source.row_count - matched_rows
        added_rows = structure.target.row_count - matched_rows

        partial = bool(structure.missing_columns or structure.extra_columns)
        result = KeyedComparison(
            structure=structure,
            key=key,
            status="partial" if partial else "complete",
            compared_columns=columns,
            matched_rows=matched_rows,
            missing_rows=missing_rows,
            added_rows=added_rows,
            modified_rows=modified_rows,
            modified_cells=modified_cells,
            reason=(
                "Only shared non-key columns were compared; schemas differ." if partial else None
            ),
        )

        if output_dir is not None:
            missing_file, added_file, modified_file = self._write_keyed_differences(
                source, target, result, output_dir
            )
            result = replace(
                result,
                missing_rows_file=missing_file,
                added_rows_file=added_file,
                modified_cells_file=modified_file,
            )
        return result

    def _write_keyed_differences(
        self, source: Dataset, target: Dataset, result: KeyedComparison, output_dir: Path
    ) -> tuple[Path, Path, Path]:
        """Write records whose identifiers exist on only one side."""
        identifier = self._quote_identifier(result.key)
        output_dir = output_dir.resolve()
        final_dir = output_dir / uuid4().hex

        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".preparing-", dir=output_dir) as temporary:
                staging_dir = Path(temporary)
                with duckdb.connect(database=":memory:") as connection:
                    connection.read_parquet(
                        [str(path) for path in source.parquet_files],
                        hive_partitioning=False,
                        union_by_name=False,
                    ).create_view("export_source")

                    connection.read_parquet(
                        [str(path) for path in target.parquet_files],
                        hive_partitioning=False,
                        union_by_name=False,
                    ).create_view("export_target")

                    connection.sql(f"""
                        SELECT s.*
                        FROM export_source AS s
                        ANTI JOIN export_target AS t
                            ON s.{identifier} = t.{identifier}
                    """).to_parquet(
                        str(staging_dir / "missing.parquet"),
                        compression="zstd",
                    )

                    connection.sql(f"""
                        SELECT t.*
                        FROM export_target AS t
                        ANTI JOIN export_source AS s
                            ON t.{identifier} = s.{identifier}
                    """).to_parquet(
                        str(staging_dir / "added.parquet"),
                        compression="zstd",
                    )

                    connection.sql(self._modified_cells_query(result)).to_parquet(
                        str(staging_dir / "modified.parquet"), compression="zstd"
                    )

                    missing_count = (
                        connection.read_parquet(
                            str(staging_dir / "missing.parquet"), hive_partitioning=False
                        )
                        .aggregate("count(*)")
                        .fetchall()[0][0]
                    )
                    added_count = (
                        connection.read_parquet(
                            str(staging_dir / "added.parquet"),
                            hive_partitioning=False,
                        )
                        .aggregate("count(*)")
                        .fetchall()[0][0]
                    )
                    modified_counts = (
                        connection.read_parquet(
                            str(staging_dir / "modified.parquet"), hive_partitioning=False
                        )
                        .aggregate("count(*), count(DISTINCT record_key)")
                        .fetchall()[0]
                    )

                    if (
                        missing_count != result.missing_rows
                        or added_count != result.added_rows
                        or modified_counts[0] != result.modified_cells
                        or modified_counts[1] != result.modified_rows
                    ):
                        raise ComparisonError(
                            "Exported record counts do not match comparison results."
                        )

                staging_dir.rename(final_dir)
        except (duckdb.Error, OSError) as error:
            raise ComparisonError("Unable to write keyed comparison differences.") from error

        return (
            final_dir / "missing.parquet",
            final_dir / "added.parquet",
            final_dir / "modified.parquet",
        )

    def _modified_cells_query(self, result: KeyedComparison) -> str:
        """Build a query returning one record per modified cell."""
        identifier = self._quote_identifier(result.key)
        if not result.compared_columns:
            return f"""
                SELECT
                    s.{identifier} AS record_key,
                    CAST(NULL AS VARCHAR) AS column_name,
                    CAST(NULL AS VARCHAR) AS source_value,
                    CAST(NULL AS VARCHAR) AS target_value,
                    CAST(NULL AS VARCHAR) AS value_type
                FROM export_source AS s
                WHERE FALSE
            """

        entries = []
        for name in result.compared_columns:
            column = self._quote_identifier(name)
            label = "'" + name.replace("'", "''") + "'"
            entries.append(
                f"({label}, "
                f"CAST(s.{column} AS VARCHAR), "
                f"CAST(t.{column} AS VARCHAR), "
                f"typeof(s.{column}), "
                f"s.{column} IS DISTINCT FROM t.{column})"
            )
        values = ", ".join(entries)

        return f"""
            SELECT
                s.{identifier} AS record_key,
                changes.column_name,
                changes.source_value,
                changes.target_value,
                changes.value_type
            FROM export_source AS s
            INNER JOIN export_target AS t
                ON s.{identifier} = t.{identifier}
            CROSS JOIN LATERAL (
                VALUES {values}
            ) AS changes(
                column_name, source_value, target_value, value_type, is_modified
            )
            WHERE changes.is_modified
        """
