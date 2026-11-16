import argparse
import csv
import multiprocessing as mp
import platform
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import psutil


def generate_files(directory: Path, rows: int) -> tuple[Path, Path]:
    """Generate equal-sized datasets with known differences."""
    source = directory / "source.csv"
    target = directory / "target.csv"
    changed = rows // 100
    header = ["id", "name", "city", "amount"]

    with source.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        for index in range(rows):
            writer.writerow([
                f"{index:010d}", f"Customer {index}", "Rennes", "100.00"
            ])

    with target.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)

        # Remove the first 1%, modify the next 1%, reverse the row order.
        for index in range(rows - 1, changed - 1, -1):
            city = "Paris" if index < 2 * changed else "Rennes"
            writer.writerow([
                f"{index:010d}", f"Customer {index}", city, "100.00"
            ])

        # Add as many records as were removed.
        for index in range(rows, rows + changed):
            writer.writerow([
                f"{index:010d}", f"Customer {index}", "Rennes", "100.00"
            ])

    return source, target


def worker(
    source: Path,
    target: Path,
    jobs_dir: Path,
    rows: int,
    mode: str,
    queue: Any,
) -> None:
    """Run one benchmark in an isolated process."""
    try:
        import duckdb
        import pyarrow

        from src.models import KeyedComparison
        from src.services.comparison_service import ComparisonService, CsvInput

        service = ComparisonService(jobs_dir)
        start = perf_counter()
        job = service.run(
            source=CsvInput(source),
            targets={"target": CsvInput(target)},
            key="id" if mode == "keyed" else None,
        )
        seconds = perf_counter() - start

        outcome = job.targets["target"]
        if outcome.error is not None or outcome.comparison is None:
            raise RuntimeError(outcome.error or "Missing comparison result.")

        result = outcome.comparison
        changed = rows // 100
        expected_missing = changed if mode == "keyed" else 2 * changed
        expected_matched = rows - expected_missing

        if (
            result.status != "complete"
            or result.matched_rows != expected_matched
            or result.missing_rows != expected_missing
            or result.added_rows != expected_missing
        ):
            raise RuntimeError("Comparison counts do not match generated data.")

        if isinstance(result, KeyedComparison):
            if result.modified_rows != changed or result.modified_cells != changed:
                raise RuntimeError("Modified counts do not match generated data.")

        disk_bytes = sum(
            path.stat().st_size for path in job.workspace.rglob("*")
            if path.is_file()
        )
        queue.put({
            "seconds": round(seconds, 3),
            "output_mib": round(disk_bytes / 1024**2, 2),
            "duckdb_version": duckdb.__version__,
            "pyarrow_version": pyarrow.__version__,
        })
    except Exception as error:
        queue.put({"error": f"{type(error).__name__}: {error}"})


def measure(
    source: Path,
    target: Path,
    jobs_dir: Path,
    rows: int,
    mode: str,
) -> dict[str, Any]:
    """Sample worker RSS every 50 milliseconds."""
    context = mp.get_context("spawn")
    queue = context.Queue()
    process = context.Process(
        target=worker,
        args=(source, target, jobs_dir, rows, mode, queue),
    )
    peak_rss = 0

    try:
        process.start()
        monitored = psutil.Process(process.pid)
        while process.is_alive():
            try:
                peak_rss = max(peak_rss, monitored.memory_info().rss)
            except psutil.NoSuchProcess:
                break
            process.join(timeout=0.05)

        process.join()
        if process.exitcode != 0:
            raise RuntimeError(f"Worker exited with code {process.exitcode}.")

        result = queue.get(timeout=10)
        if "error" in result:
            raise RuntimeError(result["error"])
        result["sampled_peak_rss_mib"] = round(peak_rss / 1024**2, 2)
        return result
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join()
            process.close()
        queue.close()
        queue.join_thread()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=[10_000, 100_000, 1_000_000]
    )
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 100 for size in args.sizes):
        parser.error("Use at least 100 rows and one repetition.")

    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    report_path = report_dir / f"benchmark_csv_{timestamp}.csv"

    with report_path.open("w", encoding="utf-8", newline="") as report:
        writer = None
        for rows in args.sizes:
            with TemporaryDirectory(prefix="dbtwin-benchmark-") as temporary:
                directory = Path(temporary)
                source, target = generate_files(directory, rows)

                for mode in ("keyed", "unkeyed"):
                    for repeat in range(1, args.repeats + 1):
                        result = measure(
                            source,
                            target,
                            directory / f"jobs_{mode}_{repeat}",
                            rows,
                            mode,
                        )
                        record = {
                            "rows_per_file": rows,
                            "columns": 4,
                            "mode": mode,
                            "repeat": repeat,
                            "input_mib": round(
                                (source.stat().st_size + target.stat().st_size)
                                / 1024**2,
                                2,
                            ),
                            **result,
                            "python_version": platform.python_version(),
                            "platform": platform.platform(),
                            "logical_cpus": psutil.cpu_count(),
                            "system_ram_gib": round(
                                psutil.virtual_memory().total / 1024**3, 2
                            ),
                        }
                        if writer is None:
                            writer = csv.DictWriter(report, fieldnames=list(record))
                            writer.writeheader()
                        writer.writerow(record)
                        report.flush()
                        print(
                            f"{rows:>10,} rows | {mode:8} | "
                            f"{result['seconds']:>8.3f} s | "
                            f"{result['sampled_peak_rss_mib']:>8.2f} MiB",
                            flush=True,
                        )

    print(f"Report: {report_path}")


if __name__ == "__main__":
    mp.freeze_support()
    main()