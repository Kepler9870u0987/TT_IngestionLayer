"""
Unit tests for lightweight secrets resolver.
"""
import pytest
import os

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.secrets import resolve_secret


class TestResolveSecret:
    """Test resolve_secret function"""

    def test_none_returns_none(self):
        assert resolve_secret(None) is None

    def test_plain_text_returned_as_is(self):
        assert resolve_secret("my-password") == "my-password"

    def test_empty_string(self):
        assert resolve_secret("") == ""

    def test_file_scheme_reads_file(self, tmp_path):
        """Test file: scheme reads secret from file"""
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("super-secret-password\n")

        result = resolve_secret(f"file:{secret_file}")
        assert result == "super-secret-password"

    def test_file_scheme_strips_whitespace(self, tmp_path):
        """Test trailing whitespace/newlines stripped"""
        secret_file = tmp_path / "secret.txt"
        secret_file.write_text("  password  \n\n")

        result = resolve_secret(f"file:{secret_file}")
        assert result == "password"

    def test_file_scheme_missing_file_raises(self):
        """Test FileNotFoundError on missing file"""
        with pytest.raises(FileNotFoundError, match="Secret file not found"):
            resolve_secret("file:/nonexistent/path/secret.txt")

    def test_env_scheme_reads_env_var(self, monkeypatch):
        """Test env: scheme reads from environment"""
        monkeypatch.setenv("TEST_SECRET_VAR", "env-password")
        result = resolve_secret("env:TEST_SECRET_VAR")
        assert result == "env-password"

    def test_env_scheme_missing_var_raises(self):
        """Test KeyError when env var not set"""
        # Ensure it doesn't exist
        os.environ.pop("NONEXISTENT_SECRET_VAR_XYZ", None)
        with pytest.raises(KeyError, match="Environment variable not set"):
            resolve_secret("env:NONEXISTENT_SECRET_VAR_XYZ")

    def test_value_starting_with_file_colon_but_valid(self, tmp_path):
        """Test file: with valid path"""
        f = tmp_path / "db_pass"
        f.write_text("db123")
        result = resolve_secret(f"file:{f}")
        assert result == "db123"

    def test_plain_text_with_special_chars(self):
        """Test plain text with colons (not a scheme)"""
        result = resolve_secret("redis://host:6379")
        assert result == "redis://host:6379"
