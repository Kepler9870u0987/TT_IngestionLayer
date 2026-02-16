"""
Unit tests for scripts/restore.py

Tests backup listing, file validation, and restore logic.
"""
import os
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.restore import (
    list_backups,
    print_backups,
    validate_backup_file,
    locate_redis_rdb,
    restore_backup,
)


# -----------------------------------------------------------------------
# list_backups
# -----------------------------------------------------------------------

class TestListBackups:
    def test_sorted_newest_first(self, tmp_path):
        (tmp_path / "redis_20260201_120000.rdb").write_bytes(b"a")
        (tmp_path / "redis_20260215_120000.rdb").write_bytes(b"b")
        result = list_backups(tmp_path)
        assert result[0].name == "redis_20260215_120000.rdb"

    def test_empty_dir(self, tmp_path):
        assert list_backups(tmp_path) == []

    def test_ignores_non_rdb(self, tmp_path):
        (tmp_path / "redis_20260201_120000.rdb").write_bytes(b"a")
        (tmp_path / "notes.txt").write_text("hi")
        result = list_backups(tmp_path)
        assert len(result) == 1


# -----------------------------------------------------------------------
# print_backups (smoke test – just ensure no exception)
# -----------------------------------------------------------------------

class TestPrintBackups:
    def test_no_backups(self, tmp_path, capsys):
        print_backups(tmp_path)
        out = capsys.readouterr().out
        assert "No backups found" in out

    def test_with_backups(self, tmp_path, capsys):
        (tmp_path / "redis_20260201_120000.rdb").write_bytes(b"a" * 100)
        print_backups(tmp_path)
        out = capsys.readouterr().out
        assert "redis_20260201_120000.rdb" in out


# -----------------------------------------------------------------------
# validate_backup_file
# -----------------------------------------------------------------------

class TestValidateBackupFile:
    def test_valid_rdb(self, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"REDIS0009some_data")
        assert validate_backup_file(f) is True

    def test_non_rdb_header_warns(self, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"NOTRD0009some_data")
        # Still returns True (just warns)
        assert validate_backup_file(f) is True

    def test_empty_file(self, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"")
        assert validate_backup_file(f) is False

    def test_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.rdb"
        assert validate_backup_file(f) is False

    def test_directory_not_file(self, tmp_path):
        d = tmp_path / "folder"
        d.mkdir()
        assert validate_backup_file(d) is False


# -----------------------------------------------------------------------
# locate_redis_rdb
# -----------------------------------------------------------------------

class TestLocateRedisRdb:
    @patch("redis.Redis")
    def test_returns_path(self, mock_redis_cls, tmp_path):
        client = MagicMock()
        mock_redis_cls.return_value = client
        client.ping.return_value = True
        client.config_get.side_effect = lambda k: {
            "dir": {"dir": str(tmp_path)},
            "dbfilename": {"dbfilename": "dump.rdb"},
        }[k]

        result = locate_redis_rdb("localhost", 6379, None, 0)
        assert result == tmp_path / "dump.rdb"

    @patch("redis.Redis")
    def test_returns_none_on_error(self, mock_redis_cls):
        mock_redis_cls.side_effect = Exception("nope")
        result = locate_redis_rdb("localhost", 6379, None, 0)
        assert result is None


# -----------------------------------------------------------------------
# restore_backup – dry_run mode
# -----------------------------------------------------------------------

class TestRestoreBackupDryRun:
    def test_dry_run_valid_file(self, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"REDIS0009data")

        result = restore_backup(
            backup_file=f,
            dry_run=True,
        )
        assert result is True

    def test_dry_run_invalid_file(self, tmp_path):
        f = tmp_path / "missing.rdb"
        result = restore_backup(
            backup_file=f,
            dry_run=True,
        )
        assert result is False


# -----------------------------------------------------------------------
# restore_backup – manual mode (no --force)
# -----------------------------------------------------------------------

class TestRestoreBackupManual:
    def test_manual_prints_instructions(self, tmp_path, capsys):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"REDIS0009data")

        result = restore_backup(backup_file=f, dry_run=False, force=False)
        assert result is True
        out = capsys.readouterr().out
        assert "MANUAL RESTORE INSTRUCTIONS" in out

    def test_manual_invalid_file(self, tmp_path):
        f = tmp_path / "nope.rdb"
        result = restore_backup(backup_file=f, dry_run=False, force=False)
        assert result is False


# -----------------------------------------------------------------------
# restore_backup – force mode
# -----------------------------------------------------------------------

class TestRestoreBackupForce:
    def test_force_rejects_remote_host(self, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"REDIS0009data")

        result = restore_backup(
            backup_file=f,
            host="10.0.0.5",
            force=True,
        )
        assert result is False

    @patch("scripts.restore.locate_redis_rdb", return_value=None)
    def test_force_no_rdb_path(self, mock_locate, tmp_path):
        f = tmp_path / "dump.rdb"
        f.write_bytes(b"REDIS0009data")

        result = restore_backup(
            backup_file=f,
            host="localhost",
            force=True,
        )
        assert result is False
