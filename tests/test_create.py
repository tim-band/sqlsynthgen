"""Tests for the main module."""
from unittest import TestCase
from unittest.mock import MagicMock, patch

from sqlsynthgen.create import create_db_tables, generate
from tests.utils import get_test_settings


class MyTestCase(TestCase):
    """Module test case."""

    def test_generate(self) -> None:
        """Test the generate function."""
        with patch("sqlsynthgen.create.populate") as mock_populate, patch(
            "sqlsynthgen.create.get_settings"
        ) as mock_get_settings, patch(
            "sqlsynthgen.create.create_engine"
        ) as mock_create_engine:
            mock_get_settings.return_value = get_test_settings()

            generate([], [])

            mock_populate.assert_called_once()
            mock_create_engine.assert_called_once()

    def test_create_tables(self) -> None:
        """Test the create_tables function."""
        mock_meta = MagicMock()

        with patch("sqlsynthgen.create.create_engine") as mock_create_engine, patch(
            "sqlsynthgen.create.get_settings"
        ) as mock_get_settings:

            create_db_tables(mock_meta)
            mock_get_settings.assert_called_once()
            mock_create_engine.assert_called_once_with(
                mock_get_settings.return_value.dst_postgres_dsn
            )