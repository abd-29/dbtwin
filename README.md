# dbtwin

**Compare datasets across files. Identify missing records, added records, and changed values.**

dbtwin compares a reference dataset against one or more targets through a Streamlit interface. It supports CSV, Parquet, and Excel files, with or without a unique record identifier.

The comparison engine runs independently of the interface and uses DuckDB over an internal Parquet representation.

## Features

- **Multiple targets** — compare each target independently against one reference.
- **Multiple formats** — load CSV, Parquet, and Excel files.
- **CSV inspection** — detect common delimiters and Unicode encodings, with manual overrides.
- **Excel worksheets** — select a worksheet independently for each workbook.
- **Key-based comparison** — identify missing records, added records, and modified cells.
- **Comparison without a key** — compare complete rows while accounting for duplicate occurrences.
- **Schema inspection** — report missing columns, additional columns, and stored type differences.
- **Result exports** — download differences as CSV or Parquet.
- **Isolated failures** — an invalid target does not prevent other targets from being compared.

## Quick start

### Requirements

- Python 3.12, used in CI
- Poetry 2.x

### Install and run

From the repository root:

```bash
poetry install --with dev
poetry run streamlit run app.py
```

### Compare datasets

1. Open **Load data**.
2. Select the source and target file formats.
3. Upload one reference file and one or more targets.
4. For Excel files, select the worksheets to compare.
5. Click **Load files** and inspect the data in **Explore data**.
6. Select a record identifier, or choose **No key**.
7. Click **Compare**.
8. Review the results and download the differences.

Source and target formats can differ. All targets in a single upload selection use the selected target format.

## Supported inputs

| Format | Reading options | Value handling |
|---|---|---|
| CSV | Encoding and delimiter | Text values |
| Parquet | No CSV-specific settings | Stored types preserved |
| Excel `.xlsx` | Worksheet selection | Text representation of cell values |

### CSV

Automatic inspection supports common delimiters:

- Comma
- Semicolon
- Tab
- Pipe

The reader recognizes UTF-8 and Unicode byte-order marks. Other encodings can be selected manually.

Detection uses a sample and may require correction. The first record is treated as the header. Malformed records are rejected rather than silently skipped.

### Parquet

Parquet inputs preserve their stored types, including integers, booleans, dates, decimals, and null values.

### Excel

The first row of the selected worksheet is treated as the header. If no worksheet is specified through the Python service, the first worksheet is used.

- Cell values are converted to text.
- Dates and times use ISO representations.
- Blank cells remain null.
- Formulas and Excel error cells are rejected.
- Display formatting is not applied.

For example, a numeric cell containing `123` and formatted to display `00123` is read as `123`. An identifier stored as the text `00123` is preserved.

## Comparison semantics

### With a record identifier

The selected key must:

- Exist in both datasets.
- Contain no null, empty, or whitespace-only identifiers.
- Be unique within each dataset.

Records are matched by key. Shared non-key columns are compared using exact values.

| Result | Meaning |
|---|---|
| Matched rows | Identifiers present in both datasets |
| Missing rows | Identifiers present only in the source |
| Added rows | Identifiers present only in the target |
| Modified rows | Matched records with at least one changed cell |
| Modified cells | Individual value differences within matched records |

Different column sets produce a **partial comparison**. Only shared non-key columns are compared.

Type differences in shared columns prevent value comparison. dbtwin does not silently convert incompatible types.

### Without a record identifier

Complete rows and their occurrence counts are compared.

If a row occurs three times in the source and once in the target, two occurrences are reported as missing.

This mode requires matching column names and types. It cannot determine whether an individual cell was modified: a changed row is reported as a missing row and an added row.

### Equality rules

- Row order does not matter.
- Column order does not matter.
- Column names must match exactly.
- Spaces and letter case are significant.
- Two null values are equivalent.
- An empty string is distinct from a null value.
- No automatic trimming, case conversion, or numeric tolerance is applied.

A complete comparison with no structural or value differences is reported as **Identical**. Matching dimensions alone do not establish equality.

### Cross-format comparisons

Values must have compatible stored types.

CSV and Excel ingestion use textual representations, while Parquet preserves native types. A textual `"25"` and a Parquet integer `25` are therefore reported as a type difference.

## Results and exports

Results include source and target dimensions, comparison counters, schema differences, and downloadable detail files.

| File | Content |
|---|---|
| `missing.parquet` | Records or occurrences missing from the target |
| `added.parquet` | Records or occurrences added in the target |
| `modified.parquet` | One row per modified cell in key-based comparisons |

Modified-cell exports contain:

```text
record_key
column_name
source_value
target_value
value_type
```

Source and target values in this detail file are represented as text. Null values remain null, and the original prepared datasets retain their stored types.

CSV downloads use `\N` as the null marker. A literal `\N` can therefore be ambiguous in a CSV export; use Parquet when that distinction matters.

The interface displays up to 100 preview rows. Downloads contain the complete result.

## Architecture

```text
src/
├── ingestion/          # Format-specific readers
├── transformation/     # Internal Parquet preparation
├── comparison/         # DuckDB comparison engine
├── services/           # Multi-target orchestration
├── models.py           # Dataset and result contracts
└── exceptions.py       # Application errors

ui/
├── components.py       # Shared interface components
├── theme.py            # Styling and branding
└── pages/              # Loading, exploration, and results

tests/
├── unit/
└── integration/

benchmarks/              # Standalone performance measurements
```

Readers produce Arrow batches. The normalizer writes the internal Parquet datasets. DuckDB performs comparisons and writes differences without collecting complete datasets into Python objects.

Streamlit handles user interaction. The ingestion, normalization, comparison, and service layers do not depend on it.

## Configuration and storage

`config.py` defines paths, supported file formats, and available ingestion modes.

Runtime files use a temporary workspace by default. Set `DBTWIN_WORKSPACE` to choose another location.

PowerShell:

```powershell
$env:DBTWIN_WORKSPACE = "C:\dbtwin-workspace"
```

Bash:

```bash
export DBTWIN_WORKSPACE="/var/tmp/dbtwin"
```

Each comparison job receives an isolated directory. Failed preparation outputs are cleaned up. Automatic retention and deletion of completed jobs are not implemented.

The application does not overwrite input files.

## Development

Install development dependencies:

```bash
poetry install --with dev
```

Run the checks:

```bash
poetry run ruff format --check src ui tests app.py config.py
poetry run ruff check src ui tests app.py config.py
poetry run mypy src ui app.py config.py
poetry run pytest -q
```

Tests generate their own datasets and temporary files. They do not depend on the demonstration files in `data/`.

GitHub Actions runs formatting, lint, type checks, and tests on Windows and Linux with Python 3.12.

PyArrow currently has a scoped missing-type-information exception in mypy. Its integration is verified through runtime tests.

## Performance

The following local benchmark compares one source and one target, each containing four textual columns.

The target includes:

- 1% removed records
- 1% added records
- 1% modified records
- Reversed row order

Durations include ingestion, Parquet preparation, comparison, and difference writing. Input generation, browser upload, interface rendering, and downloads are excluded.

### Median duration across three runs

| Rows per file | With key | Without key |
|---:|---:|---:|
| 10,000 | 0.381 s | 0.238 s |
| 100,000 | 0.515 s | 0.491 s |
| 1,000,000 | 2.179 s | 2.451 s |

### Highest sampled process RSS across those runs

| Rows per file | With key | Without key |
|---:|---:|---:|
| 10,000 | 92 MiB | 99 MiB |
| 100,000 | 146 MiB | 200 MiB |
| 1,000,000 | 600 MiB | 734 MiB |

Memory is sampled every 50 ms and includes native library allocations. Short-lived peaks may be missed.

These results describe one CSV scenario on one machine. They are not guaranteed limits and do not establish Excel or Parquet input performance. Environment details are recorded in the raw report under `reports/`.

Reproduce the benchmark:

```bash
poetry run python -m benchmarks.benchmark_csv \
    --sizes 10000 100000 1000000 \
    --repeats 3
```

## Current limitations

- Database connections are not implemented.
- Composite keys and column-name mapping are not implemented.
- CSV and Excel values are not automatically converted to inferred business types.
- Text normalization and numeric tolerances are not implemented.
- Excel formulas are not evaluated.
- Streamlit uploads and downloads use server memory.
- Completed workspaces require an external cleanup policy.
- A standalone API and production authentication are not provided.

## Roadmap

- Explicit type inference and conversion rules
- Column mapping and composite keys
- Configurable comparison rules
- Database connectors, starting with PostgreSQL
- Workspace retention controls
- Reusable comparison configurations
- Deployment through an interface-independent API

## Contributing

For bug reports, include the input formats, reading settings, comparison mode, expected behavior, and a minimal reproducible example.

Use synthetic or anonymized data. Do not include credentials or confidential datasets.

Changes should include relevant tests and pass the development checks.

## License

See [LICENSE](LICENSE).