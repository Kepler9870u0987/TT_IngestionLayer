#!/usr/bin/env python3
"""
Redis Backup Automation Script.

Triggers a Redis BGSAVE, waits for completion, and copies the RDB dump file
to a timestamped backup in an output directory.  Optionally prunes backups
older than a configurable retention period.

Usage:
    python scripts/backup.py
    python scripts/backup.py --output-dir ./backups --retention-days 30
    python scripts/backup.py --redis-host 10.0.0.5 --redis-port 6380
"""
import os
import sys
import time
import shutil
import argparse
import glob
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is on sys.path so imports work when invoked directly
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.common.logging_config import get_logger

logger = get_logger(__name__)


def _connect_redis(host: str, port: int, password: str | None, db: int):
    """
    Create a raw ``redis.Redis`` connection (no project wrapper needed).
    """
    import redis
    return redis.Redis(
        host=host, port=port, password=password, db=db,
        decode_responses=True, socket_connect_timeout=5,
    )


def trigger_bgsave(client, timeout: int = 120) -> bool:
    """
    Issue ``BGSAVE`` and poll ``LASTSAVE`` until it changes.

    Args:
        client: redis.Redis instance
        timeout: Maximum seconds to wait for the save to finish

    Returns:
        True if save completed within timeout
    """
    last_save_before = client.lastsave()
    logger.info(f"Current LASTSAVE timestamp: {last_save_before}")

    client.bgsave()
    logger.info("BGSAVE initiated – waiting for completion…")

    deadline = time.time() + timeout
    while time.time() < deadline:
        current = client.lastsave()
        if current != last_save_before:
            logger.info(f"BGSAVE completed at {current}")
            return True
        time.sleep(1)

    logger.error(f"BGSAVE did not complete within {timeout}s")
    return False


def locate_rdb_file(client) -> Path | None:
    """
    Determine the RDB file path from the Redis ``CONFIG GET dir / dbfilename``.
    """
    try:
        rdb_dir = client.config_get("dir").get("dir", ".")
        rdb_name = client.config_get("dbfilename").get("dbfilename", "dump.rdb")
        rdb_path = Path(rdb_dir) / rdb_name
        if rdb_path.exists():
            return rdb_path
        logger.warning(f"RDB path {rdb_path} does not exist on this host")
        return None
    except Exception as exc:
        logger.error(f"Could not determine RDB path: {exc}")
        return None


def copy_backup(rdb_path: Path, output_dir: Path) -> Path | None:
    """
    Copy the RDB file to ``output_dir`` with a timestamped name.

    Returns:
        Path of the created backup file, or None on failure
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dest = output_dir / f"redis_{ts}.rdb"

    try:
        shutil.copy2(str(rdb_path), str(dest))
        size_mb = dest.stat().st_size / (1024 * 1024)
        logger.info(f"Backup saved: {dest}  ({size_mb:.2f} MB)")
        return dest
    except Exception as exc:
        logger.error(f"Failed to copy RDB to {dest}: {exc}")
        return None


def prune_old_backups(output_dir: Path, retention_days: int) -> int:
    """
    Delete backup files older than *retention_days*.

    Returns:
        Number of files removed.
    """
    cutoff = datetime.utcnow() - timedelta(days=retention_days)
    removed = 0
    for f in sorted(output_dir.glob("redis_*.rdb")):
        mtime = datetime.utcfromtimestamp(f.stat().st_mtime)
        if mtime < cutoff:
            f.unlink()
            logger.info(f"Pruned old backup: {f.name}")
            removed += 1
    if removed:
        logger.info(f"Pruned {removed} backup(s) older than {retention_days} days")
    return removed


def list_backups(output_dir: Path) -> list[Path]:
    """Return list of existing backup files sorted newest-first."""
    backups = sorted(output_dir.glob("redis_*.rdb"), reverse=True)
    return backups


def run_backup(
    host: str = "localhost",
    port: int = 6379,
    password: str | None = None,
    db: int = 0,
    output_dir: str = "backups",
    retention_days: int = 30,
    timeout: int = 120,
) -> bool:
    """
    Full backup workflow: BGSAVE → copy → prune.

    Returns:
        True on success
    """
    out = Path(output_dir)
    client = _connect_redis(host, port, password, db)

    # Verify connection
    try:
        client.ping()
        logger.info(f"Connected to Redis at {host}:{port}")
    except Exception as exc:
        logger.error(f"Cannot connect to Redis: {exc}")
        return False

    # Trigger BGSAVE
    if not trigger_bgsave(client, timeout=timeout):
        return False

    # Locate and copy
    rdb_path = locate_rdb_file(client)
    if rdb_path is None:
        logger.warning(
            "Cannot locate RDB file on this host.  "
            "If Redis runs in a container, mount its data volume and use "
            "--rdb-path to specify the path explicitly."
        )
        return False

    dest = copy_backup(rdb_path, out)
    if dest is None:
        return False

    # Prune old backups
    prune_old_backups(out, retention_days)

    # List current backups
    backups = list_backups(out)
    logger.info(f"Backups on disk: {len(backups)}")
    for b in backups[:5]:
        size = b.stat().st_size / (1024 * 1024)
        logger.info(f"  {b.name}  ({size:.2f} MB)")

    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Redis Backup – trigger BGSAVE and copy RDB to backup dir"
    )
    parser.add_argument(
        "--redis-host", default="localhost", help="Redis host (default: localhost)"
    )
    parser.add_argument(
        "--redis-port", type=int, default=6379, help="Redis port (default: 6379)"
    )
    parser.add_argument(
        "--redis-password", default=None, help="Redis password"
    )
    parser.add_argument(
        "--redis-db", type=int, default=0, help="Redis database (default: 0)"
    )
    parser.add_argument(
        "--output-dir", default="backups",
        help="Directory to store backup files (default: backups/)"
    )
    parser.add_argument(
        "--retention-days", type=int, default=30,
        help="Delete backups older than N days (default: 30)"
    )
    parser.add_argument(
        "--timeout", type=int, default=120,
        help="Max seconds to wait for BGSAVE (default: 120)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List existing backups and exit"
    )

    args = parser.parse_args()

    if args.list:
        backups = list_backups(Path(args.output_dir))
        if not backups:
            print("No backups found.")
        else:
            print(f"{'File':<40} {'Size (MB)':>10} {'Date':<20}")
            print("-" * 72)
            for b in backups:
                size = b.stat().st_size / (1024 * 1024)
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                print(f"{b.name:<40} {size:>10.2f} {mtime:<20}")
        return 0

    ok = run_backup(
        host=args.redis_host,
        port=args.redis_port,
        password=args.redis_password,
        db=args.redis_db,
        output_dir=args.output_dir,
        retention_days=args.retention_days,
        timeout=args.timeout,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
