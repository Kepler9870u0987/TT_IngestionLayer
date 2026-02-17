#!/usr/bin/env python3
"""
Redis Restore Script.

Lists available backups and provides instructions (or automated steps where
safe) for restoring a Redis RDB dump.

Usage:
    python scripts/restore.py --list
    python scripts/restore.py --file backups/redis_20260217_120000.rdb
    python scripts/restore.py --file backups/redis_20260217_120000.rdb --dry-run
"""
import os
import sys
import shutil
import argparse
from datetime import datetime
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from src.common.logging_config import get_logger

logger = get_logger(__name__)


def list_backups(backup_dir: Path) -> list[Path]:
    """Return backup files sorted newest-first."""
    backups = sorted(backup_dir.glob("redis_*.rdb"), reverse=True)
    return backups


def print_backups(backup_dir: Path) -> None:
    """Pretty-print available backups."""
    backups = list_backups(backup_dir)
    if not backups:
        print(f"No backups found in {backup_dir}")
        return

    print(f"\nAvailable backups in {backup_dir}:\n")
    print(f"  {'#':<4} {'File':<40} {'Size (MB)':>10} {'Date':<20}")
    print(f"  {'-'*74}")
    for i, b in enumerate(backups, 1):
        size = b.stat().st_size / (1024 * 1024)
        mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        print(f"  {i:<4} {b.name:<40} {size:>10.2f} {mtime:<20}")
    print()


def validate_backup_file(path: Path) -> bool:
    """Check that the backup file exists and has a reasonable size."""
    if not path.exists():
        logger.error(f"Backup file not found: {path}")
        return False
    if not path.is_file():
        logger.error(f"Path is not a file: {path}")
        return False
    size = path.stat().st_size
    if size == 0:
        logger.error(f"Backup file is empty: {path}")
        return False
    # RDB files start with "REDIS" magic bytes
    try:
        with open(path, "rb") as f:
            header = f.read(5)
        if header != b"REDIS":
            logger.warning(
                f"File does not appear to be a valid RDB dump "
                f"(header: {header!r}).  Proceeding anyway."
            )
    except Exception as exc:
        logger.warning(f"Could not read file header: {exc}")
    return True


def locate_redis_rdb(host: str, port: int, password: str | None, db: int) -> Path | None:
    """Query Redis for the current RDB file location."""
    try:
        import redis
        client = redis.Redis(
            host=host, port=port, password=password, db=db,
            decode_responses=True, socket_connect_timeout=5,
        )
        client.ping()
        rdb_dir = client.config_get("dir").get("dir", ".")  # type: ignore[union-attr]
        rdb_name = client.config_get("dbfilename").get("dbfilename", "dump.rdb")  # type: ignore[union-attr]
        return Path(rdb_dir) / rdb_name
    except Exception as exc:
        logger.error(f"Could not query Redis for RDB path: {exc}")
        return None


def restore_backup(
    backup_file: Path,
    host: str = "localhost",
    port: int = 6379,
    password: str | None = None,
    db: int = 0,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Restore a Redis backup.

    For safety this script:
    1. Validates the backup file.
    2. Locates the current RDB path via ``CONFIG GET``.
    3. Prints step-by-step manual instructions (stop Redis → copy → restart).
    4. If ``--force`` is passed **and** Redis is local, it will attempt an
       automated ``DEBUG RELOAD`` after replacing the RDB file.  Use with
       care in production.

    Args:
        backup_file: Path to the .rdb backup
        dry_run: If True, only show what would be done
        force: If True, attempt automated restore (local Redis only)

    Returns:
        True if validations pass (or restore completed in force mode)
    """
    if not validate_backup_file(backup_file):
        return False

    size_mb = backup_file.stat().st_size / (1024 * 1024)
    logger.info(f"Backup file: {backup_file}  ({size_mb:.2f} MB)")

    rdb_target = locate_redis_rdb(host, port, password, db)

    if dry_run:
        logger.info("[DRY RUN] Would restore backup – no changes made")
        if rdb_target:
            logger.info(f"[DRY RUN] Target RDB path: {rdb_target}")
        _print_manual_instructions(backup_file, rdb_target, host, port)
        return True

    if not force:
        _print_manual_instructions(backup_file, rdb_target, host, port)
        return True

    # ---------- force mode: automated restore ----------
    if rdb_target is None:
        logger.error("Cannot determine RDB path – manual restore required")
        return False

    if host not in ("localhost", "127.0.0.1"):
        logger.error(
            "Automated restore only supported for local Redis.  "
            "Use manual steps for remote hosts."
        )
        return False

    logger.warning("⚠  Force mode: replacing RDB and reloading Redis…")

    try:
        import redis as _redis
        client = _redis.Redis(
            host=host, port=port, password=password, db=db,
            decode_responses=True,
        )

        # Shutdown save first
        client.config_set("save", "")

        # Copy backup over current RDB
        shutil.copy2(str(backup_file), str(rdb_target))
        logger.info(f"Copied {backup_file.name} → {rdb_target}")

        # Reload
        client.execute_command("DEBUG", "RELOAD")
        logger.info("Redis reloaded with restored data ✓")

        # Re-enable default save policy
        client.config_set("save", "3600 1 300 100 60 10000")
        return True

    except Exception as exc:
        logger.error(f"Automated restore failed: {exc}")
        logger.info("Attempting manual restore instructions instead:")
        _print_manual_instructions(backup_file, rdb_target, host, port)
        return False


def _print_manual_instructions(
    backup_file: Path,
    rdb_target: Path | None,
    host: str,
    port: int,
) -> None:
    """Print step-by-step manual restore instructions."""
    target_str = str(rdb_target) if rdb_target else "/var/lib/redis/dump.rdb"

    print("\n" + "=" * 60)
    print("  MANUAL RESTORE INSTRUCTIONS")
    print("=" * 60)
    print()
    print("  1. Stop the Redis server:")
    print(f"       redis-cli -h {host} -p {port} SHUTDOWN NOSAVE")
    print()
    print("  2. Replace the RDB file:")
    print(f"       cp {backup_file} {target_str}")
    print()
    print("  3. Start the Redis server:")
    print("       redis-server /etc/redis/redis.conf")
    print("       # or: systemctl start redis")
    print()
    print("  4. Verify data:")
    print(f"       redis-cli -h {host} -p {port} PING")
    print(f"       redis-cli -h {host} -p {port} DBSIZE")
    print()
    print("=" * 60 + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Redis Restore – list backups or restore an RDB dump"
    )
    parser.add_argument(
        "--file", type=Path, default=None,
        help="Path to the .rdb backup file to restore"
    )
    parser.add_argument(
        "--backup-dir", type=Path, default=Path("backups"),
        help="Directory containing backup files (default: backups/)"
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available backups and exit"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Attempt automated restore (local Redis only, use with caution)"
    )
    parser.add_argument(
        "--redis-host", default="localhost", help="Redis host"
    )
    parser.add_argument(
        "--redis-port", type=int, default=6379, help="Redis port"
    )
    parser.add_argument(
        "--redis-password", default=None, help="Redis password"
    )
    parser.add_argument(
        "--redis-db", type=int, default=0, help="Redis database"
    )

    args = parser.parse_args()

    if args.list:
        print_backups(args.backup_dir)
        return 0

    if args.file is None:
        print_backups(args.backup_dir)
        parser.error("--file is required for restore (or use --list)")
        return 1

    ok = restore_backup(
        backup_file=args.file,
        host=args.redis_host,
        port=args.redis_port,
        password=args.redis_password,
        db=args.redis_db,
        dry_run=args.dry_run,
        force=args.force,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
