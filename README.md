# dbtwin

Compare a reference dataset against multiple targets.

The first release supports CSV files, with or without a unique record identifier.

## Features

- Upload one source and multiple targets.
- Detect common CSV delimiters and Unicode encodings.
- Override encoding and delimiter settings.
- Preserve leading zeros, spaces, empty strings and literal values.
- Compare column names, stored types and dataset dimensions.
- Compare records using an optional unique key.
- Compare complete rows without a key, including duplicate occurrences.
- Export missing records, added records and modified cells.
- Keep target failures independent.
- Browse results through a Streamlit interface.

## Installation

Python 3.12 is used in CI.

Install Poetry, then run from the repository root:

```bash
poetry install --with dev
poetry run streamlit run app.py
```

## Usage

1. Open **Load data**.
2. Upload a source CSV and one or more targets.
3. Adjust reading settings if needed, then click **Load files**.
4. Select a record identifier or keep **No key**.
5. Click **Compare**.
6. Browse results and download differences.

**Explore data** displays up to 100 rows from a selected dataset.

## Comparison rules

### With a key

The selected column must exist, contain no empty identifiers and be unique
in both datasets.

Records are matched by identifier. Shared non-key columns are compared
using exact values.

- Missing identifiers produce missing records.
- New identifiers produce added records.
- Changed values produce modified cells.
- Different column sets produce a partial comparison.
- Type differences in shared columns prevent value comparison.

### Without a key

Complete rows and their occurrence counts are compared.

If a row occurs three times in the source and once in the target,
two occurrences are reported as missing.

Matching column names and types are required.
Modified cells cannot be inferred without a record identifier.

### Values

Row and column order do not affect equality.

CSV values are ingested as text. Spaces and letter case matter.
Empty strings remain distinct from null values.

In modified-cell exports, values are represented as text alongside their type.
CSV exports use `\N` to represent null values; Parquet preserves nulls directly.
A literal `\N` can therefore be ambiguous in the CSV export.

## Architecture

- `src/ingestion/`: source inspection and incremental reading.
- `src/transformation/`: internal Parquet preparation.
- `src/comparison/`: DuckDB comparison engine.
- `src/services/`: multi-target orchestration.
- `ui/`: Streamlit pages and components.

The comparison engine and service do not depend on Streamlit.

Runtime files are stored outside the repository by default.
Set `DBTWIN_WORKSPACE` to select another working directory.

## Checks

```bash
poetry run ruff format --check src ui tests app.py config.py
poetry run ruff check src ui tests app.py config.py
poetry run mypy src ui app.py config.py
poetry run pytest -q
```

CI runs these checks on Windows and Linux.

PyArrow currently has a scoped missing-type-information exception in mypy.
Its integration is covered by runtime tests.

## Benchmark

Local benchmark with one source, one target and four textual columns.

The target contains 1% removed records, 1% added records and 1% modified records.
Times include ingestion, Parquet preparation, comparison and difference writing.
Input generation, browser upload and interface rendering are excluded.

Median duration across three runs:

| Rows per file | With key | Without key |
|---:|---:|---:|
| 10,000 | 0.381 s | 0.238 s |
| 100,000 | 0.515 s | 0.491 s |
| 1,000,000 | 2.179 s | 2.451 s |

Highest sampled process RSS across those runs:

| Rows per file | With key | Without key |
|---:|---:|---:|
| 10,000 | 92 MiB | 99 MiB |
| 100,000 | 146 MiB | 200 MiB |
| 1,000,000 | 600 MiB | 734 MiB |

Memory is sampled every 50 ms and includes native library allocations.
Short-lived peaks may be missed.

These results describe one machine and one scenario, not guaranteed limits.
The raw report in `reports/` includes environment information.

Reproduce:

```bash
poetry run python -m benchmarks.benchmark_csv --sizes 10000 100000 1000000 --repeats 3
```

## Current limitations

- CSV files only; database connections, Excel and Parquet inputs are planned.
- The first CSV record is treated as the header.
- Encoding and delimiter detection may require manual correction.
- CSV type inference, column mapping and comparison tolerances are not implemented.
- No composite keys.
- Malformed records are rejected rather than silently skipped.
- Data previews are bounded, but Streamlit uploads and downloads use memory.
- Workspace retention and automatic cleanup of completed jobs are not implemented.

## License

See [LICENSE](LICENSE).