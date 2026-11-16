import os
from pathlib import Path
from tempfile import gettempdir

# Repository paths.
PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
TESTS_DIR = PROJECT_ROOT / "tests"
DOCS_DIR = PROJECT_ROOT / "docs"

# Runtime storage can be configured independently of the repository.
_workspace = os.getenv("DBTWIN_WORKSPACE")
WORKSPACE_DIR = (
    Path(_workspace).expanduser().resolve()
    if _workspace
    else Path(gettempdir()) / "dbtwin"
)

# Each comparison job gets its own isolated workspace.
JOBS_DIR = WORKSPACE_DIR / "jobs"

INGESTION_MODES = {
    "file": {"label": "File upload", "enabled": True},
    "postgresql": {"label": "PostgreSQL", "enabled": False},
    "mysql": {"label": "MySQL", "enabled": False},
    "sqlserver": {"label": "SQL Server", "enabled": False},
}

FILETYPE = {
    "csv": {"label": "CSV", "extensions": ["csv"], "enabled": True},
    "excel": {"label": "Excel", "extensions": ["xlsx"], "enabled": False},
    "parquet": {"label": "Parquet", "extensions": ["parquet"], "enabled": False},
}