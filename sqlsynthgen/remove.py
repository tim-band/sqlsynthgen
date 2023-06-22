"""Functions and classes to undo the operations in create.py."""
from types import ModuleType

from sqlalchemy import delete

from sqlsynthgen.settings import get_settings
from sqlsynthgen.utils import create_db_engine


def remove_db_data(orm_module: ModuleType, ssg_module: ModuleType) -> None:
    """Truncate the synthetic data tables but not the vocabularies."""
    settings = get_settings()

    assert settings.dst_postgres_dsn, "Missing destination database settings"
    dst_engine = create_db_engine(
        settings.dst_postgres_dsn, schema_name=settings.dst_schema
    )

    with dst_engine.connect() as dst_conn:
        for table in reversed(orm_module.Base.metadata.sorted_tables):
            # We presume that all tables that aren't vocab should be truncated
            if table.name not in ssg_module.vocab_dict:
                dst_conn.execute(delete(table))
