from dataclasses import replace
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.comparison.duckdb_comparator import DuckDBComparator
from src.exceptions import ComparisonError
from src.models import Dataset, DatasetOrigin
from src.transformation.normalizer import ParquetNormalizer


def prepare_dataset(batch: pa.RecordBatch, output_dir: Path, name: str) -> Dataset:
    """Create a prepared dataset from synthetic records."""
    return ParquetNormalizer().normalize(
        batches=[batch],
        output_dir=output_dir,
        name=name,
        origin=DatasetOrigin(
            ingestion_mode="file",
            location=f"{name}.csv",
            file_format="csv",
        ),
    )


def test_reports_dataset_dimensions(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001", "002"], "name": ["Alice", "Bob"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["001"], "name": ["Alice"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_structure(source, target)

    assert result.source.row_count == 2
    assert result.target.row_count == 1
    assert result.source.column_count == 2
    assert result.target.column_count == 2
    assert result.same_schema is True
    assert result.same_dimensions is False


def test_reports_missing_and_extra_columns(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "name": ["Alice"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["001"], "city": ["Rennes"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_structure(source, target)

    assert result.common_columns == ("id",)
    assert result.missing_columns == ("name",)
    assert result.extra_columns == ("city",)
    assert result.same_schema is False
    assert result.same_dimensions is True


def test_ignores_column_order(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "name": ["Alice"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"name": ["Bob"], "id": ["002"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_structure(source, target)

    assert result.same_schema is True
    assert result.same_dimensions is True
    assert result.type_differences == ()


def test_reports_column_type_differences(tmp_path: Path) -> None:
    source = prepare_dataset(pa.record_batch({"age": ["25"]}), tmp_path, "source")
    target = prepare_dataset(pa.record_batch({"age": [25]}), tmp_path, "target")

    result = DuckDBComparator().compare_structure(source, target)

    assert result.same_schema is False
    assert len(result.type_differences) == 1
    difference = result.type_differences[0]
    assert difference.name == "age"
    assert difference.source_type == "VARCHAR"
    assert difference.target_type == "BIGINT"


def test_handles_empty_datasets(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": pa.array([], type=pa.string())})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")

    result = DuckDBComparator().compare_structure(source, target)

    assert result.source.row_count == 0
    assert result.target.row_count == 0
    assert result.same_schema is True
    assert result.same_dimensions is True


def test_rejects_missing_parquet_file(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")
    target.parquet_files[0].unlink()

    with pytest.raises(ComparisonError, match="Parquet file is missing"):
        DuckDBComparator().compare_structure(source, target)


def test_rejects_incorrect_row_count_metadata(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")
    invalid_target = replace(target, row_count=100)

    with pytest.raises(ComparisonError, match="row count does not match"):
        DuckDBComparator().compare_structure(source, invalid_target)


def test_rejects_incorrect_column_metadata(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")
    invalid_column = replace(target.columns[0], name="other_id")
    invalid_target = replace(target, columns=(invalid_column,))

    with pytest.raises(ComparisonError, match="columns do not match"):
        DuckDBComparator().compare_structure(source, invalid_target)


def test_unkeyed_comparison_ignores_row_and_column_order(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001", "002"], "name": ["Alice", "Bob"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"name": ["Bob", "Alice"], "id": ["002", "001"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.status == "complete"
    assert result.matched_rows == 2
    assert result.missing_rows == 0
    assert result.added_rows == 0
    assert result.identical is True


def test_unkeyed_comparison_counts_duplicate_occurrences(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"name": ["Alice", "Alice", "Alice", "Bob"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"name": ["Alice", "Bob", "Bob"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.matched_rows == 2
    assert result.missing_rows == 2
    assert result.added_rows == 1
    assert result.identical is False


def test_unkeyed_comparison_does_not_infer_modified_records(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "city": ["Rennes"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["001"], "city": ["Paris"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.matched_rows == 0
    assert result.missing_rows == 1
    assert result.added_rows == 1
    assert result.identical is False


def test_unkeyed_comparison_handles_empty_datasets(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": pa.array([], type=pa.string())})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.status == "complete"
    assert result.matched_rows == 0
    assert result.missing_rows == 0
    assert result.added_rows == 0
    assert result.identical is True


@pytest.mark.parametrize("empty_source", [True, False])
def test_unkeyed_comparison_handles_one_empty_dataset(
    tmp_path: Path, empty_source: bool
) -> None:
    empty = pa.record_batch({"id": pa.array([], type=pa.string())})
    populated = pa.record_batch({"id": ["001", "002"]})
    source = prepare_dataset(
        empty if empty_source else populated, tmp_path, "source"
    )
    target = prepare_dataset(
        populated if empty_source else empty, tmp_path, "target"
    )

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.matched_rows == 0
    assert result.missing_rows == (0 if empty_source else 2)
    assert result.added_rows == (2 if empty_source else 0)
    assert result.identical is False


@pytest.mark.parametrize(
    "target_batch",
    [
        pa.record_batch({"other_id": ["001"]}),
        pa.record_batch({"id": [1]}),
    ],
    ids=["different-columns", "different-types"],
)
def test_unkeyed_comparison_reports_incompatible_schemas(
    tmp_path: Path, target_batch: pa.RecordBatch
) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"]}), tmp_path, "source"
    )
    target = prepare_dataset(target_batch, tmp_path, "target")

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.status == "not_compared"
    assert result.reason is not None
    assert result.matched_rows is None
    assert result.missing_rows is None
    assert result.added_rows is None
    assert result.identical is False


def test_unkeyed_comparison_distinguishes_null_empty_and_literal_na(
    tmp_path: Path,
) -> None:
    source = prepare_dataset(
        pa.record_batch({"value": pa.array([None, "", "NA"], type=pa.string())}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"value": pa.array([None, "", ""], type=pa.string())}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.matched_rows == 2
    assert result.missing_rows == 1
    assert result.added_rows == 1


def test_unkeyed_comparison_handles_special_column_names(tmp_path: Path) -> None:
    batch = pa.record_batch({
        "customer name": ["Alice"],
        'a"b': ["001"],
        "select": ["active"],
    })
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.identical is True
    assert result.matched_rows == 1


def test_writes_exact_difference_occurrences(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"name": ["Alice", "Alice", "Alice", "Bob"]}),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"name": ["Alice", "Bob", "Bob", "Charlie"]}),
        tmp_path / "datasets",
        "target",
    )

    result = DuckDBComparator().compare_without_key(
        source, target, output_dir=tmp_path / "differences"
    )

    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    missing = pq.read_table(result.missing_rows_file)
    added = pq.read_table(result.added_rows_file)
    assert Counter(missing.column("name").to_pylist()) == {"Alice": 2}
    assert Counter(added.column("name").to_pylist()) == {"Bob": 1, "Charlie": 1}
    assert missing.num_rows == result.missing_rows == 2
    assert added.num_rows == result.added_rows == 2
    assert result.missing_rows_file.parent == result.added_rows_file.parent


def test_writes_empty_differences_with_schema(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"], "name": ["Alice"]})
    source = prepare_dataset(batch, tmp_path / "datasets", "source")
    target = prepare_dataset(batch, tmp_path / "datasets", "target")

    result = DuckDBComparator().compare_without_key(
        source, target, output_dir=tmp_path / "differences"
    )

    assert result.identical is True
    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    for path in (result.missing_rows_file, result.added_rows_file):
        table = pq.read_table(path)
        assert table.num_rows == 0
        assert table.schema.names == batch.schema.names
        assert [field.type for field in table.schema] == [
            field.type for field in batch.schema
        ]


def test_does_not_write_files_when_output_is_not_requested(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")
    existing_paths = set(tmp_path.rglob("*"))

    result = DuckDBComparator().compare_without_key(source, target)

    assert result.missing_rows_file is None
    assert result.added_rows_file is None
    assert set(tmp_path.rglob("*")) == existing_paths


def test_does_not_write_differences_for_incompatible_schemas(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"]}), tmp_path / "datasets", "source"
    )
    target = prepare_dataset(
        pa.record_batch({"other_id": ["001"]}), tmp_path / "datasets", "target"
    )
    output_dir = tmp_path / "differences"

    result = DuckDBComparator().compare_without_key(
        source, target, output_dir=output_dir
    )

    assert result.status == "not_compared"
    assert result.missing_rows_file is None
    assert result.added_rows_file is None
    assert not output_dir.exists()


def test_repeated_comparisons_do_not_overwrite_results(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"]}), tmp_path / "datasets", "source"
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["002"]}), tmp_path / "datasets", "target"
    )
    comparator = DuckDBComparator()
    output_dir = tmp_path / "differences"

    first = comparator.compare_without_key(source, target, output_dir)
    second = comparator.compare_without_key(source, target, output_dir)

    assert first.missing_rows_file is not None
    assert second.missing_rows_file is not None
    assert first.missing_rows_file.parent != second.missing_rows_file.parent
    assert first.missing_rows_file.is_file()
    assert second.missing_rows_file.is_file()
    assert len(list(output_dir.iterdir())) == 2


def test_cleans_staging_directory_when_publication_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = prepare_dataset(pa.record_batch({"id": ["001"]}), tmp_path / "datasets", "source")
    target = prepare_dataset(pa.record_batch({"id": ["002"]}), tmp_path / "datasets", "target")
    output_dir = tmp_path / "differences"
    output_dir.mkdir()
    existing = output_dir / "existing.txt"
    existing.write_text("Keep this file.", encoding="utf-8")

    def fail_rename(self: Path, target: Path) -> Path:
        raise OSError("Simulated publication failure.")

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ComparisonError, match="Unable to compare or write"):
        DuckDBComparator().compare_without_key(source, target, output_dir)

    assert list(output_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "Keep this file."


def test_accepts_unique_record_keys(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": ["001", "002", "003"]}),
        tmp_path,
        "customers",
    )

    DuckDBComparator().validate_key(dataset, "id")


def test_rejects_missing_key_column(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"name": ["Alice"]}), tmp_path, "customers"
    )

    with pytest.raises(ComparisonError, match="Column 'id' is missing"):
        DuckDBComparator().validate_key(dataset, "id")


@pytest.mark.parametrize("key", ["", "   "])
def test_rejects_empty_key_name(tmp_path: Path, key: str) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": ["001"]}), tmp_path, "customers"
    )

    with pytest.raises(ComparisonError, match="key must not be empty"):
        DuckDBComparator().validate_key(dataset, key)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_rejects_missing_key_values(tmp_path: Path, value: str | None) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": pa.array(["001", value], type=pa.string())}),
        tmp_path,
        "customers",
    )

    with pytest.raises(ComparisonError, match=r"1 row\(s\) without an identifier"):
        DuckDBComparator().validate_key(dataset, "id")


def test_counts_all_rows_with_duplicate_keys(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": ["001", "001", "002", "002", "002", "003"]}),
        tmp_path,
        "customers",
    )

    with pytest.raises(
        ComparisonError, match=r"5 row\(s\) with duplicate identifiers"
    ):
        DuckDBComparator().validate_key(dataset, "id")


def test_reports_empty_and_duplicate_keys_together(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": ["001", "001", ""]}),
        tmp_path,
        "customers",
    )

    with pytest.raises(ComparisonError) as captured:
        DuckDBComparator().validate_key(dataset, "id")

    message = str(captured.value)
    assert "1 row(s) without an identifier" in message
    assert "2 row(s) with duplicate identifiers" in message


def test_accepts_empty_dataset_with_key_column(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": pa.array([], type=pa.string())}),
        tmp_path,
        "customers",
    )

    DuckDBComparator().validate_key(dataset, "id")


def test_does_not_normalize_key_values(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({"id": ["001", "001 ", "1"]}),
        tmp_path,
        "customers",
    )
    original = dataset.parquet_files[0].read_bytes()

    DuckDBComparator().validate_key(dataset, "id")

    assert dataset.parquet_files[0].read_bytes() == original


def test_handles_special_key_column_name(tmp_path: Path) -> None:
    dataset = prepare_dataset(
        pa.record_batch({'customer "id"': ["001", "002"]}),
        tmp_path,
        "customers",
    )

    DuckDBComparator().validate_key(dataset, 'customer "id"')


def test_keyed_comparison_reports_record_changes(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002"],
            "name": ["Alice", "Bob"],
            "city": ["Rennes", "Vannes"],
        }),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({
            "id": ["001", "003"],
            "name": ["Alice", "Fatou"],
            "city": ["Paris", "Nantes"],
        }),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.status == "complete"
    assert result.compared_columns == ("name", "city")
    assert result.matched_rows == 1
    assert result.missing_rows == 1
    assert result.added_rows == 1
    assert result.modified_rows == 1
    assert result.modified_cells == 1
    assert result.identical is False


def test_keyed_comparison_counts_modified_rows_and_cells(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003"],
            "name": ["Alice", "Bob", "Fatou"],
            "city": ["Rennes", "Vannes", "Nantes"],
        }),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003"],
            "name": ["Alicia", "Bob", "Fatou"],
            "city": ["Paris", "Lyon", "Nantes"],
        }),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.matched_rows == 3
    assert result.modified_rows == 2
    assert result.modified_cells == 3
    assert result.missing_rows == 0
    assert result.added_rows == 0


def test_keyed_comparison_ignores_row_and_column_order(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001", "002"], "name": ["Alice", "Bob"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"name": ["Bob", "Alice"], "id": ["002", "001"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.identical is True
    assert result.matched_rows == 2
    assert result.modified_rows == 0
    assert result.modified_cells == 0


def test_keyed_comparison_handles_null_values(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003"],
            "value": pa.array([None, None, ""], type=pa.string()),
        }),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003"],
            "value": pa.array([None, "", None], type=pa.string()),
        }),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.modified_rows == 2
    assert result.modified_cells == 2


def test_keyed_comparison_reports_partial_schema_match(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({
            "id": ["001"], "name": ["Alice"], "city": ["Rennes"]
        }),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({
            "id": ["001"], "name": ["Alicia"], "status": ["active"]
        }),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.status == "partial"
    assert result.compared_columns == ("name",)
    assert result.structure.missing_columns == ("city",)
    assert result.structure.extra_columns == ("status",)
    assert result.modified_rows == 1
    assert result.modified_cells == 1
    assert result.reason is not None
    assert result.identical is False


@pytest.mark.parametrize(
    "target_batch",
    [
        pa.record_batch({"id": [1], "value": ["10"]}),
        pa.record_batch({"id": ["001"], "value": [10]}),
    ],
    ids=["key-type-difference", "value-type-difference"],
)
def test_keyed_comparison_rejects_implicit_type_conversion(
    tmp_path: Path, target_batch: pa.RecordBatch
) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "value": ["10"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(target_batch, tmp_path, "target")

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.status == "not_compared"
    assert result.matched_rows is None
    assert result.modified_cells is None
    assert result.identical is False


@pytest.mark.parametrize("invalid_side", ["source", "target"])
def test_keyed_comparison_validates_both_datasets(
    tmp_path: Path, invalid_side: str
) -> None:
    valid = pa.record_batch({"id": ["001"]})
    invalid = pa.record_batch({"id": ["001", "001"]})
    source = prepare_dataset(
        invalid if invalid_side == "source" else valid, tmp_path, "source"
    )
    target = prepare_dataset(
        invalid if invalid_side == "target" else valid, tmp_path, "target"
    )

    with pytest.raises(ComparisonError, match="duplicate identifiers"):
        DuckDBComparator().compare_with_key(source, target, "id")


def test_keyed_comparison_handles_key_only_datasets(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001", "002"]}), tmp_path, "source"
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["002", "001"]}), tmp_path, "target"
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.compared_columns == ()
    assert result.matched_rows == 2
    assert result.modified_rows == 0
    assert result.modified_cells == 0
    assert result.identical is True


@pytest.mark.parametrize("empty_side", ["source", "target", "both"])
def test_keyed_comparison_handles_empty_datasets(
    tmp_path: Path, empty_side: str
) -> None:
    empty = pa.record_batch({"id": pa.array([], type=pa.string())})
    populated = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(
        empty if empty_side in ("source", "both") else populated,
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        empty if empty_side in ("target", "both") else populated,
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.status == "complete"
    assert result.matched_rows == 0
    assert result.missing_rows == (1 if empty_side == "target" else 0)
    assert result.added_rows == (1 if empty_side == "source" else 0)
    assert result.modified_rows == 0
    assert result.modified_cells == 0
    assert result.identical is (empty_side == "both")


def test_keyed_comparison_handles_special_column_names(tmp_path: Path) -> None:
    key = 'customer "id"'
    source = prepare_dataset(
        pa.record_batch({key: ["001"], "select": ["old"]}),
        tmp_path,
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({key: ["001"], "select": ["new"]}),
        tmp_path,
        "target",
    )

    result = DuckDBComparator().compare_with_key(source, target, key)

    assert result.matched_rows == 1
    assert result.modified_rows == 1
    assert result.modified_cells == 1


def test_keyed_export_contains_only_missing_and_added_identifiers(
    tmp_path: Path,
) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001", "002"], "city": ["Rennes", "Vannes"]}),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["001", "003"], "city": ["Paris", "Nantes"]}),
        tmp_path / "datasets",
        "target",
    )

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=tmp_path / "differences"
    )

    assert result.modified_rows == 1
    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    assert pq.read_table(result.missing_rows_file).to_pylist() == [
        {"id": "002", "city": "Vannes"}
    ]
    assert pq.read_table(result.added_rows_file).to_pylist() == [
        {"id": "003", "city": "Nantes"}
    ]
    assert result.missing_rows_file.parent == result.added_rows_file.parent


def test_keyed_export_writes_empty_files_with_columns(tmp_path: Path) -> None:
    batch = pa.record_batch({"id": ["001"], "name": ["Alice"]})
    source = prepare_dataset(batch, tmp_path / "datasets", "source")
    target = prepare_dataset(batch, tmp_path / "datasets", "target")

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=tmp_path / "differences"
    )

    assert result.identical is True
    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    for path in (result.missing_rows_file, result.added_rows_file):
        table = pq.read_table(path)
        assert table.num_rows == 0
        assert table.schema.names == batch.schema.names
        assert [field.type for field in table.schema] == [
            field.type for field in batch.schema
        ]


def test_keyed_export_preserves_each_side_columns(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "city": ["Rennes"]}),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["002"], "status": ["active"]}),
        tmp_path / "datasets",
        "target",
    )

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=tmp_path / "differences"
    )

    assert result.status == "partial"
    assert result.missing_rows_file is not None
    assert result.added_rows_file is not None
    assert pq.read_table(result.missing_rows_file).to_pylist() == [
        {"id": "001", "city": "Rennes"}
    ]
    assert pq.read_table(result.added_rows_file).to_pylist() == [
        {"id": "002", "status": "active"}
    ]


def test_keyed_comparison_without_export_does_not_create_files(
    tmp_path: Path,
) -> None:
    batch = pa.record_batch({"id": ["001"]})
    source = prepare_dataset(batch, tmp_path, "source")
    target = prepare_dataset(batch, tmp_path, "target")
    existing_paths = set(tmp_path.rglob("*"))

    result = DuckDBComparator().compare_with_key(source, target, "id")

    assert result.missing_rows_file is None
    assert result.added_rows_file is None
    assert set(tmp_path.rglob("*")) == existing_paths


def test_keyed_export_skips_incompatible_types(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"]}), tmp_path / "datasets", "source"
    )
    target = prepare_dataset(
        pa.record_batch({"id": [1]}), tmp_path / "datasets", "target"
    )
    output_dir = tmp_path / "differences"

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=output_dir
    )

    assert result.status == "not_compared"
    assert result.missing_rows_file is None
    assert result.added_rows_file is None
    assert not output_dir.exists()


def test_keyed_export_cleans_partial_output_on_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = prepare_dataset(pa.record_batch({"id": ["001"]}), tmp_path / "datasets", "source")
    target = prepare_dataset(pa.record_batch({"id": ["002"]}), tmp_path / "datasets", "target")
    output_dir = tmp_path / "differences"
    output_dir.mkdir()
    existing = output_dir / "existing.txt"
    existing.write_text("Keep this file.", encoding="utf-8")

    def fail_rename(self: Path, target: Path) -> Path:
        raise OSError("Simulated publication failure.")

    monkeypatch.setattr(Path, "rename", fail_rename)

    with pytest.raises(ComparisonError, match="Unable to write keyed"):
        DuckDBComparator().compare_with_key(source, target, "id", output_dir=output_dir)

    assert list(output_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "Keep this file."


def test_modified_export_preserves_exact_values(tmp_path: Path) -> None:
    source = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003", "004"],
            "city": pa.array(["Rennes", None, "", "Vannes"], type=pa.string()),
        }),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({
            "id": ["001", "002", "003", "005"],
            "city": pa.array(["Paris", "", None, "Nantes"], type=pa.string()),
        }),
        tmp_path / "datasets",
        "target",
    )

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=tmp_path / "differences"
    )

    assert result.modified_cells_file is not None
    rows = pq.read_table(result.modified_cells_file).to_pylist()
    assert sorted(rows, key=lambda row: row["record_key"]) == [
        {
            "record_key": "001",
            "column_name": "city",
            "source_value": "Rennes",
            "target_value": "Paris",
            "value_type": "VARCHAR",
        },
        {
            "record_key": "002",
            "column_name": "city",
            "source_value": None,
            "target_value": "",
            "value_type": "VARCHAR",
        },
        {
            "record_key": "003",
            "column_name": "city",
            "source_value": "",
            "target_value": None,
            "value_type": "VARCHAR",
        },
    ]
    assert result.modified_rows == result.modified_cells == len(rows) == 3
    assert result.missing_rows == 1
    assert result.added_rows == 1


def test_modified_export_handles_multiple_cells_and_special_names(
    tmp_path: Path,
) -> None:
    key = 'customer "id"'
    column = 'customer\'s "age"'
    source = prepare_dataset(
        pa.record_batch({key: ["001"], column: [25], "name": ["Alice"]}),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({key: ["001"], column: [26], "name": ["Alicia"]}),
        tmp_path / "datasets",
        "target",
    )

    result = DuckDBComparator().compare_with_key(
        source, target, key, output_dir=tmp_path / "differences"
    )

    assert result.modified_cells_file is not None
    rows = pq.read_table(result.modified_cells_file).to_pylist()
    changes = {row["column_name"]: row for row in rows}
    assert len(rows) == result.modified_cells == 2
    assert result.modified_rows == 1
    assert changes[column] == {
        "record_key": "001",
        "column_name": column,
        "source_value": "25",
        "target_value": "26",
        "value_type": "BIGINT",
    }
    assert changes["name"]["source_value"] == "Alice"
    assert changes["name"]["target_value"] == "Alicia"


@pytest.mark.parametrize("key_only", [True, False])
def test_modified_export_preserves_schema_when_empty(
    tmp_path: Path, key_only: bool
) -> None:
    batch = (
        pa.record_batch({"id": ["001"]})
        if key_only
        else pa.record_batch({"id": ["001"], "name": ["Alice"]})
    )
    source = prepare_dataset(batch, tmp_path / "datasets", "source")
    target = prepare_dataset(batch, tmp_path / "datasets", "target")

    result = DuckDBComparator().compare_with_key(
        source, target, "id", output_dir=tmp_path / "differences"
    )

    assert result.modified_cells_file is not None
    table = pq.read_table(result.modified_cells_file)
    assert table.num_rows == 0
    assert table.column_names == [
        "record_key", "column_name", "source_value", "target_value", "value_type"
    ]
    assert result.modified_cells == 0
    assert result.identical is True


def test_modified_export_failure_removes_all_partial_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = prepare_dataset(
        pa.record_batch({"id": ["001"], "name": ["Alice"]}),
        tmp_path / "datasets",
        "source",
    )
    target = prepare_dataset(
        pa.record_batch({"id": ["001"], "name": ["Alicia"]}),
        tmp_path / "datasets",
        "target",
    )
    output_dir = tmp_path / "differences"
    comparator = DuckDBComparator()

    # Fail the third export after the first two files have been written.
    monkeypatch.setattr(
        comparator,
        "_modified_cells_query",
        lambda result: "SELECT * FROM nonexistent_test_relation",
    )

    with pytest.raises(ComparisonError, match="Unable to write keyed"):
        comparator.compare_with_key(source, target, "id", output_dir)

    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []