"""Tests for the main module."""
import os
from io import StringIO
from pathlib import Path
from subprocess import CalledProcessError
from unittest import TestCase
from unittest.mock import call, patch

import yaml
from sqlalchemy import Column, Integer, create_engine, insert
from sqlalchemy.orm import declarative_base

from sqlsynthgen import make
from sqlsynthgen.make import make_tables_file
from tests.examples import example_orm
from tests.utils import RequiresDBTestCase, run_psql

# pylint: disable=invalid-name
Base = declarative_base()
# pylint: enable=invalid-name
metadata = Base.metadata


class MakeTable(Base):  # type: ignore
    """A SQLAlchemy model."""

    __tablename__ = "maketable"
    id = Column(
        Integer,
        primary_key=True,
    )


class TestsThatRequireDB(RequiresDBTestCase):
    """Tests that require a database."""

    def setUp(self) -> None:
        """Pre-test setup."""

        run_psql("providers.dump")

        self.engine = create_engine(
            "postgresql://postgres:password@localhost:5432/providers",
        )
        metadata.create_all(self.engine)
        os.chdir("tests/examples")

    def tearDown(self) -> None:
        """Post-test cleanup."""
        os.chdir("../..")

    def test__download_table(self) -> None:
        """Test the _download_table function."""
        # pylint: disable=protected-access
        with self.engine.connect() as conn:
            conn.execute(insert(MakeTable).values({"id": 1}))

        make._download_table(MakeTable.__table__, self.engine)

        with Path("expected.csv").open(encoding="utf-8") as csvfile:
            expected = csvfile.read()

        with Path("maketable.csv").open(encoding="utf-8") as csvfile:
            actual = csvfile.read()

        self.assertEqual(expected, actual)


class TestMake(TestCase):
    """Tests that don't require a database."""

    def setUp(self) -> None:
        """Pre-test setup."""

        os.chdir("tests/examples")

    def tearDown(self) -> None:
        """Post-test cleanup."""
        os.chdir("../..")

    def test_make_generators_from_tables(self) -> None:
        """Check that we can make a generators file from a tables module."""
        self.maxDiff = None  # pylint: disable=invalid-name
        with open("expected_ssg.py", encoding="utf-8") as expected_output:
            expected = expected_output.read()
        conf_path = "generator_conf.yaml"
        with open(conf_path, "r", encoding="utf8") as f:
            config = yaml.safe_load(f)

        with patch("sqlsynthgen.make._download_table",) as mock_download, patch(
            "sqlsynthgen.make.create_engine"
        ) as mock_create_engine, patch("sqlsynthgen.make.get_settings"):
            actual = make.make_generators_from_tables(example_orm, config)
            mock_download.assert_called_once()
            mock_create_engine.assert_called_once()

        self.assertEqual(expected, actual)

    def test_make_tables_file(self) -> None:
        """Test the make_tables_file function."""

        with patch("sqlsynthgen.make.run") as mock_run, patch(
            "sqlsynthgen.make.Path", spec=True
        ) as mock_path:
            mock_run.return_value.stdout = "some output"
            mock_path.return_value.exists.return_value = False

            make_tables_file("my:postgres/db", None)

            self.assertEqual(
                call(
                    [
                        "sqlacodegen",
                        "my:postgres/db",
                    ],
                    capture_output=True,
                    encoding="utf-8",
                    check=True,
                ),
                mock_run.call_args_list[0],
            )
            mock_path.assert_called_once_with("orm.py")
            mock_path.return_value.write_text.assert_called_once_with(
                "some output", encoding="utf-8"
            )

    def test_make_tables_file_with_schema(self) -> None:
        """Check that the function handles the schema setting."""
        with patch("sqlsynthgen.make.run") as mock_run, patch(
            "sqlsynthgen.make.Path"
        ) as mock_path:
            mock_path.return_value.exists.return_value = False

            make_tables_file("my:postgres/db", "my_schema")

            self.assertEqual(
                call(
                    [
                        "sqlacodegen",
                        "--schema=my_schema",
                        "my:postgres/db",
                    ],
                    capture_output=True,
                    encoding="utf-8",
                    check=True,
                ),
                mock_run.call_args_list[0],
            )

    def test_make_tables_handles_errors(self) -> None:
        """Test the make-tables sub-command handles sqlacodegen errors."""

        class SysExit(Exception):
            """To force the function to exit as sys.exit() would."""

        with patch("sqlsynthgen.make.run") as mock_run, patch(
            "sqlsynthgen.make.stderr", new_callable=StringIO
        ) as mock_stderr, patch("sys.exit") as mock_exit, patch(
            "sqlsynthgen.make.Path"
        ) as mock_path:
            mock_path.return_value.exists.return_value = False
            mock_run.side_effect = CalledProcessError(
                returncode=99, cmd="some-cmd", stderr="some-error-output"
            )
            mock_exit.side_effect = SysExit

            try:
                make_tables_file("my:postgres/db", None)
            except SysExit:
                pass

            mock_exit.assert_called_once_with(99)
            self.assertEqual("some-error-output\n", mock_stderr.getvalue())

    def test_make_tables_warns_no_pk(self) -> None:
        """Test the make-tables sub-command warns about Tables()."""

        with patch("sqlsynthgen.make.run") as mock_run, patch(
            "sqlsynthgen.make.stderr", new_callable=StringIO
        ) as mock_stderr, patch("sqlsynthgen.make.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            mock_run.return_value.stdout = "t_nopk_table = Table("
            make_tables_file("my:postgres/db", None)

        self.assertEqual(
            "WARNING: Table without PK detected. sqlsynthgen may not be able to continue.\n",
            mock_stderr.getvalue(),
        )

    def test_make_tables_errors_if_file_exists(self) -> None:
        """Test that we abort if an orm file already exists."""

        class SysExit(Exception):
            """To force the function to exit as sys.exit() would."""

        with patch(
            "sqlsynthgen.make.stderr", new_callable=StringIO
        ) as mock_stderr, patch("sys.exit") as mock_exit, patch(
            "sqlsynthgen.make.Path", spec=True
        ) as mock_path:
            mock_path.return_value.exists.return_value = True

            mock_exit.side_effect = SysExit

            try:
                make_tables_file("my:postgres/db", None)
            except SysExit:
                pass

            mock_exit.assert_called_once_with(1)
            self.assertEqual(
                "orm.py should not already exist. Exiting...\n", mock_stderr.getvalue()
            )
