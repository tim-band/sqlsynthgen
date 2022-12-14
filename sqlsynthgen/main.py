"""Entrypoint for the sqlsynthgen package."""
import importlib
from typing import Any, Final

from sqlalchemy import create_engine, insert
from sqlalchemy.sql import sqltypes

from sqlsynthgen.settings import Settings
from sqlsynthgen.star import AdvanceDecision, metadata
from sqlsynthgen.star_gens import AdvanceDecisionGenerator

settings = Settings()


def main() -> None:
    """Create an empty schema and populate it with dummy data."""
    engine = create_engine(settings.postgres_dsn)
    populate(engine)

    return
    metadata.create_all(bind=engine)


def populate(engine: Any) -> None:
    """Populate a database schema with dummy data."""
    # for table in metadata.sorted_tables:
    # print(dir(table))
    # print(table.name)
    # print(table.columns[0].type)
    # return

    stmt = insert(AdvanceDecision).values(AdvanceDecisionGenerator().__dict__)
    with engine.connect() as conn:
        conn.execute(stmt)


def create_generators_from_tables(tables_module_name: str) -> str:
    """Creates sqlsynthgen generator classes from a sqlacodegen-generated file.

    Args:
      tables_module_name: The name of a sqlacodegen-generated module.
        It should be in "module" or "package.module" format.

    Returns:
      A string that is a valid Python module, once written to file.
    """

    new_content = (
        '"""This file was auto-generated by sqlsynthgen but can be edited manually."""\n'
        "from mimesis import Generic\n"
        + "from mimesis.locales import Locale\n"
        + "generic = Generic(locale=Locale.EN)\n"
    )
    sorted_generators = "["
    indentation: Final[str] = "    "

    sql_to_mimesis_map = {
        sqltypes.Integer: "generic.numeric.integer_number()",
        sqltypes.BigInteger: "generic.numeric.integer_number()",
        sqltypes.DateTime: "generic.datetime.datetime()",
    }

    tables_module = importlib.import_module(tables_module_name)
    for table in tables_module.metadata.sorted_tables:
        # print(f"\n{table.name}")
        new_class_name = table.name + "Generator"
        sorted_generators += new_class_name + ", "
        new_content += (
            "\n\nclass "
            + new_class_name
            + ":\n"
            + indentation
            + "def __init__(self):\n"
        )

        for column in table.columns:
            # We presume that primary keys are populated automatically
            if not column.primary_key:
                new_content += (
                    indentation * 2
                    + "self."
                    + column.name
                    + " = "
                    + sql_to_mimesis_map[type(column.type)]
                    + "\n"
                )
            # print(column)

    sorted_generators += "]"

    new_content += "\n\n" + "sorted_generators = " + sorted_generators + "\n"

    return new_content


if __name__ == "__main__":
    main()
