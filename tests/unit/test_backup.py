"""
Unit tests for scripts/backup.py

Tests BGSAVE trigger, RDB copy, retention pruning, and listing.
"""
import os
import sys
import time
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.backup import (
    trigger_bgsave,
    locate_rdb_file,
    copy_backup,
    prune_old_backups,
    list_backups,
    run_backup,
)


# -----------------------------------------------------------------------
# trigger_bgsave
# -----------------------------------------------------------------------

class TestTriggerBgsave:
    def test_bgsave_succeeds(self):
        client = MagicMock()
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        t1 = datetime(2026, 1, 1, 0, 0, 5)
        client.lastsave.side_effect = [t0, t0, t1]

        result = trigger_bgsave(client, timeout=10)
        assert result is True
        client.bgsave.assert_called_once()

    def test_bgsave_timeout(self):
        client = MagicMock()
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        client.lastsave.return_value = t0  # never changes

        result = trigger_bgsave(client, timeout=1)
        assert result is False


# -----------------------------------------------------------------------
# locate_rdb_file
# -----------------------------------------------------------------------

class TestLocateRdbFile:
    def test_returns_path_when_exists(self, tmp_path):
        rdb = tmp_path / "dump.rdb"
        rdb.write_text("fake")

        client = MagicMock()
        client.config_get.side_effect = lambda k: {
            "dir": {"dir": str(tmp_path)},
            "dbfilename": {"dbfilename": "dump.rdb"},
        }[k]

        result = locate_rdb_file(client)
        assert result == rdb

    def test_returns_none_when_missing(self, tmp_path):
        client = MagicMock()
        client.config_get.side_effect = lambda k: {
            "dir": {"dir": str(tmp_path)},
            "dbfilename": {"dbfilename": "dump.rdb"},
        }[k]

        result = locate_rdb_file(client)
        assert result is None

    def test_returns_none_on_exception(self):
        client = MagicMock()
        client.config_get.side_effect = Exception("no perms")
        result = locate_rdb_file(client)
        assert result is None


# -----------------------------------------------------------------------
# copy_backup
# -----------------------------------------------------------------------

class TestCopyBackup:
    def test_copies_file(self, tmp_path):
        rdb = tmp_path / "source" / "dump.rdb"
        rdb.parent.mkdir()
        rdb.write_bytes(b"REDIS0009")

        out_dir = tmp_path / "backups"
        dest = copy_backup(rdb, out_dir)

        assert dest is not None
        assert dest.exists()
        assert dest.name.startswith("redis_")
        assert dest.name.endswith(".rdb")
        assert dest.read_bytes() == b"REDIS0009"

    def test_creates_output_dir(self, tmp_path):
        rdb = tmp_path / "dump.rdb"
        rdb.write_bytes(b"data")

        out_dir = tmp_path / "new" / "nested" / "dir"
        dest = copy_backup(rdb, out_dir)
        assert dest is not None
        assert out_dir.exists()


# -----------------------------------------------------------------------
# prune_old_backups
# -----------------------------------------------------------------------

class TestPruneOldBackups:
    def test_prunes_old_files(self, tmp_path):
        # Create old file
        old = tmp_path / "redis_20250101_000000.rdb"
        old.write_bytes(b"old")
        # Fake old mtime
        old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
        os.utime(old, (old_ts, old_ts))

        # Create recent file
        recent = tmp_path / "redis_20260217_120000.rdb"
        recent.write_bytes(b"new")

        removed = prune_old_backups(tmp_path, retention_days=30)
        assert removed == 1
        assert not old.exists()
        assert recent.exists()

    def test_keeps_all_within_retention(self, tmp_path):
        f = tmp_path / "redis_20260217_120000.rdb"
        f.write_bytes(b"recent")

        removed = prune_old_backups(tmp_path, retention_days=30)
        assert removed == 0
        assert f.exists()


# -----------------------------------------------------------------------
# list_backups
# -----------------------------------------------------------------------

class TestListBackups:
    def test_lists_sorted(self, tmp_path):
        (tmp_path / "redis_20260201_120000.rdb").write_bytes(b"a")
        (tmp_path / "redis_20260215_120000.rdb").write_bytes(b"b")
        (tmp_path / "redis_20260210_120000.rdb").write_bytes(b"c")
        (tmp_path / "other_file.txt").write_text("ignore")

        result = list_backups(tmp_path)
        assert len(result) == 3
        assert result[0].name == "redis_20260215_120000.rdb"
        assert result[2].name == "redis_20260201_120000.rdb"

    def test_empty_dir(self, tmp_path):
        assert list_backups(tmp_path) == []


# -----------------------------------------------------------------------
# run_backup (integration-style with mocks)
# -----------------------------------------------------------------------

class TestRunBackup:
    @patch("scripts.backup._connect_redis")
    def test_run_backup_full(self, mock_connect, tmp_path):
        client = MagicMock()
        mock_connect.return_value = client
        client.ping.return_value = True

        t0 = datetime(2026, 1, 1)
        t1 = datetime(2026, 1, 2)
        client.lastsave.side_effect = [t0, t1]

        rdb = tmp_path / "rdb" / "dump.rdb"
        rdb.parent.mkdir()
        rdb.write_bytes(b"REDIS0009data")

        client.config_get.side_effect = lambda k: {
            "dir": {"dir": str(rdb.parent)},
            "dbfilename": {"dbfilename": "dump.rdb"},
        }[k]

        out = tmp_path / "backups"
        result = run_backup(
            output_dir=str(out),
            timeout=10,
            retention_days=30,
        )

        assert result is True
        assert len(list_backups(out)) == 1

    @patch("scripts.backup._connect_redis")
    def test_run_backup_connect_fail(self, mock_connect, tmp_path):
        client = MagicMock()
        mock_connect.return_value = client
        client.ping.side_effect = Exception("conn refused")

        result = run_backup(output_dir=str(tmp_path / "backups"))
        assert result is False
