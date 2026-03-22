"""One-time migration: Parquet files → DuckDB.

Design doc: docs/plans/DUCKDB_INTRADAY_BACKTEST.md, Phase 1.7.

Usage:
    uv run python scripts/migrate_parquet_to_duckdb.py
        [--data-dir ./data/ohlcv] [--db-path ./data/backtest.duckdb]
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import polars as pl

# Add parent to path so we can import the data module
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.db import DuckDBManager  # noqa: E402
from src.data.store import DataStore  # noqa: E402


def migrate(data_dir: str, db_path: str) -> None:  # noqa: PLR0915
    """Migrate all Parquet files to DuckDB.

    Args:
        data_dir: Directory containing {SYMBOL}.parquet files.
        db_path: Path to DuckDB database file.

    """
    parquet_dir = Path(data_dir)
    if not parquet_dir.exists():
        print(f"No parquet directory found at {parquet_dir}")
        return

    parquet_files = sorted(parquet_dir.glob("*.parquet"))
    if not parquet_files:
        print(f"No .parquet files found in {parquet_dir}")
        return

    print(f"Found {len(parquet_files)} Parquet files in {parquet_dir}")

    # Connect to DuckDB
    db = DuckDBManager(db_path=db_path, memory_limit="4GB")
    db.connect()
    store = DataStore(db=db)

    migrated = 0
    skipped = 0
    errors = 0

    for parquet_path in parquet_files:
        symbol = parquet_path.stem.upper()
        try:
            df = pl.read_parquet(parquet_path)
            if df.is_empty():
                print(f"  SKIP {symbol}: empty file")
                skipped += 1
                continue

            # Ensure date column exists and is sorted
            if "date" not in df.columns:
                print(f"  SKIP {symbol}: no 'date' column")
                skipped += 1
                continue

            df = df.sort("date")
            store.write_ohlcv(symbol, df, timeframe="daily")

            # Verify row count
            read_back = store.read_ohlcv(symbol, timeframe="daily")
            if read_back is None:
                print(f"  ERROR {symbol}: write succeeded but read-back returned None")
                errors += 1
                continue

            if len(read_back) != len(df):
                print(
                    f"  WARN {symbol}: row count mismatch "
                    f"(parquet={len(df)}, duckdb={len(read_back)})"
                )

            print(f"  OK {symbol}: {len(df)} rows migrated")
            migrated += 1

        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {symbol} ({type(exc).__name__}): {exc}")
            traceback.print_exc()
            errors += 1

    # Post-migration cleanup
    try:
        db.conn.execute("FORCE CHECKPOINT")
        db_size = db.db_size_bytes()
        print(f"\nDatabase size: {db_size / 1024 / 1024:.1f} MB")
    except Exception as exc:  # noqa: BLE001
        print(f"\nWARNING: Post-migration checkpoint failed: {exc}")
        print("Data was written but may not be fully persisted.")
    finally:
        db.close()

    print(f"\nMigration complete: {migrated} migrated, {skipped} skipped, {errors} errors")
    print(f"\nParquet files are preserved at {parquet_dir}")
    print("Delete them manually after verifying the migration.")


def main() -> None:
    """Parse args and run migration."""
    parser = argparse.ArgumentParser(description="Migrate Parquet files to DuckDB")
    parser.add_argument(
        "--data-dir",
        default="./data/ohlcv",
        help="Directory containing Parquet files (default: ./data/ohlcv)",
    )
    parser.add_argument(
        "--db-path",
        default="./data/backtest.duckdb",
        help="Path to DuckDB database file (default: ./data/backtest.duckdb)",
    )
    args = parser.parse_args()
    migrate(args.data_dir, args.db_path)


if __name__ == "__main__":
    main()
