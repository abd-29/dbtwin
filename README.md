# dbtwin comparison test pack

One source CSV, multiple independent target CSVs. All data is synthetic.

## Run tests

From this folder:

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

## Data folders

| Folder | Scenario |
|---|---|
| donnee1 | Identical data and reordered rows/columns |
| donnee2 | Added and removed records |
| donnee3 | Modified cells |
| donnee4 | Missing and extra columns |
| donnee5 | Invalid keys and independent target failures |
| donnee6 | Empty target with headers |
| donnee7 | Leading zeros, UTF-8, quoted text, blanks, literal NA |
| donnee8 | Empty source |

Each folder contains source.csv, target CSVs, expected.json and a README.
All files use UTF-8, comma separators and the key client_id.
These are different data scenarios, not different CSV dialects.
The current reader does not automatically detect semicolon separators or legacy encodings.
Values are compared as exact text: 10 and 10.0 differ; spaces matter; blank is distinct from NA.
Row and column order do not matter. Only shared columns are compared for cell changes.
Schema differences are reported separately. Header-only CSVs are valid; zero-byte files are not.
Target key validation errors are isolated by compare_all; CSV parsing errors occur during loading.
