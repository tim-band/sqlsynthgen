"""Functions and classes to create and populate the target database."""
from collections import Counter
from typing import Any, Generator, Mapping, Sequence, Tuple

from psycopg2.errors import UndefinedObject
from sqlalchemy import Connection, ForeignKeyConstraint, insert
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.schema import (
    AddConstraint,
    CreateSchema,
    DropConstraint,
    MetaData,
    Table,
)
from sqlsynthgen.base import FileUploader, TableGenerator
from sqlsynthgen.settings import get_settings
from sqlsynthgen.utils import (
    create_db_engine,
    get_sync_engine,
    get_vocabulary_table_names,
    logger,
    make_foreign_key_name,
)

Story = Generator[Tuple[str, dict[str, Any]], dict[str, Any], None]
RowCounts = Counter[str]


def create_db_tables(metadata: MetaData) -> None:
    """Create tables described by the sqlalchemy metadata object."""
    settings = get_settings()
    dst_dsn: str = settings.dst_dsn or ""
    assert dst_dsn != "", "Missing DST_DSN setting."

    engine = get_sync_engine(create_db_engine(dst_dsn))

    # Create schema, if necessary.
    if settings.dst_schema:
        schema_name = settings.dst_schema
        with engine.connect() as connection:
            connection.execute(CreateSchema(schema_name, if_not_exists=True))
            connection.commit()

        # Recreate the engine, this time with a schema specified
        engine = get_sync_engine(create_db_engine(dst_dsn, schema_name=schema_name))

    metadata.create_all(engine)


def create_db_vocab(metadata: MetaData, meta_dict: dict[str, Any], config: Mapping) -> int:
    """
    Load vocabulary tables from files.
    
    arguments:
    metadata: The schema of the database
    meta_dict: The simple description of the schema from --orm-file
    config: The configuration from --config-file
    """
    settings = get_settings()
    dst_dsn: str = settings.dst_dsn or ""
    assert dst_dsn != "", "Missing DST_DSN setting."

    dst_engine = get_sync_engine(
        create_db_engine(dst_dsn, schema_name=settings.dst_schema)
    )

    tables_loaded: list[str] = []

    vocab_tables = get_vocabulary_table_names(config)
    for vocab_table_name in vocab_tables:
        vocab_table = metadata.tables[vocab_table_name]
        # Remove foreign key constraints from the table
        for fk in vocab_table.foreign_key_constraints:
            logger.debug("Dropping constraint %s from table %s", fk.name, vocab_table_name)
            with Session(dst_engine) as session:
                session.begin()
                try:
                    session.execute(DropConstraint(fk))
                except IntegrityError:
                    session.rollback()
                    logger.exception("Dropping table %s key constraint %s failed:", vocab_table_name, fk.name)
                except ProgrammingError as e:
                    session.rollback()
                    if type(e.orig) is UndefinedObject:
                        logger.debug("Constraint does not exist")
                    else:
                        raise e
        # Load data into the table
        try:
            logger.debug("Loading vocabulary table %s", vocab_table_name)
            uploader = FileUploader(table=vocab_table)
            with Session(dst_engine) as session:
                session.begin()
                uploader.load(session.connection())
            session.commit()
            tables_loaded.append(vocab_table_name)
        except IntegrityError:
            logger.exception("Loading the vocabulary table %s failed:", vocab_table)
    # Now we add the constraints back to all the tables
    for vocab_table_name in vocab_tables:
        try:
            for (column_name, column_dict) in meta_dict["tables"][vocab_table_name]["columns"].items():
                fk_targets = column_dict.get("foreign_keys", [])
                if fk_targets:
                    fk = ForeignKeyConstraint(
                        columns=[column_name],
                        name=make_foreign_key_name(vocab_table_name, column_name),
                        refcolumns=fk_targets,
                    )
                    with Session(dst_engine) as session:
                        session.begin()
                        vocab_table.append_constraint(fk)
                        session.execute(AddConstraint(fk))
                        session.commit()
        except IntegrityError:
            logger.exception("Restoring table %s foreign keys failed:", vocab_table)
    return tables_loaded


def create_db_data(
    sorted_tables: Sequence[Table],
    table_generator_dict: Mapping[str, TableGenerator],
    story_generator_list: Sequence[Mapping[str, Any]],
    num_passes: int,
) -> RowCounts:
    """Connect to a database and populate it with data."""
    settings = get_settings()
    dst_dsn: str = settings.dst_dsn or ""
    assert dst_dsn != "", "Missing DST_DSN setting."

    dst_engine = get_sync_engine(
        create_db_engine(dst_dsn, schema_name=settings.dst_schema)
    )

    row_counts: Counter[str] = Counter()
    with dst_engine.connect() as dst_conn:
        for _ in range(num_passes):
            row_counts += populate(
                dst_conn,
                sorted_tables,
                table_generator_dict,
                story_generator_list,
            )
    return row_counts


def _populate_story(
    story: Story,
    table_dict: Mapping[str, Table],
    table_generator_dict: Mapping[str, TableGenerator],
    dst_conn: Connection,
) -> RowCounts:
    """Write to the database all the rows created by the given story."""
    # Loop over the rows generated by the story, insert them into their
    # respective tables. Ideally this would say
    # `for table_name, provided_values in story:`
    # but we have to loop more manually to be able to use the `send` function.
    row_counts: Counter[str] = Counter()
    try:
        table_name, provided_values = next(story)
        while True:
            table = table_dict[table_name]
            if table.name in table_generator_dict:
                table_generator = table_generator_dict[table.name]
                default_values = table_generator(dst_conn)
            else:
                default_values = {}
            insert_values = {**default_values, **provided_values}
            stmt = insert(table).values(insert_values).return_defaults()
            cursor = dst_conn.execute(stmt)
            # We need to return all the default values etc. to the generator,
            # because other parts of the story may refer to them.
            if cursor.returned_defaults:
                # pylint: disable=protected-access
                return_values = {
                    str(k): v for k, v in cursor.returned_defaults._mapping.items()
                }
                # pylint: enable=protected-access
            else:
                return_values = {}
            final_values = {**insert_values, **return_values}
            row_counts[table_name] = row_counts.get(table_name, 0) + 1
            table_name, provided_values = story.send(final_values)
    except StopIteration:
        # The story has finished, it has no more rows to generate
        pass
    return row_counts


def populate(
    dst_conn: Connection,
    tables: Sequence[Table],
    table_generator_dict: Mapping[str, TableGenerator],
    story_generator_list: Sequence[Mapping[str, Any]],
) -> RowCounts:
    """Populate a database schema with synthetic data."""
    row_counts: Counter[str] = Counter()
    table_dict = {table.name: table for table in tables}
    # Generate stories
    # Each story generator returns a python generator (an unfortunate naming clash with
    # what we call generators). Iterating over it yields individual rows for the
    # database. First, collect all of the python generators into a single list.
    stories: list[tuple[str, Story]] = sum(
        [
            [
                (sg["name"], sg["function"](dst_conn))
                for _ in range(sg["num_stories_per_pass"])
            ]
            for sg in story_generator_list
        ],
        [],
    )
    for name, story in stories:
        # Run the inserts for each story within a transaction.
        logger.debug('Generating data for story "%s".', name)
        with dst_conn.begin():
            row_counts += _populate_story(
                story, table_dict, table_generator_dict, dst_conn
            )

    # Generate individual rows, table by table.
    for table in tables:
        if table.name not in table_generator_dict:
            # We don't have a generator for this table, probably because it's a
            # vocabulary table.
            continue
        table_generator = table_generator_dict[table.name]
        if table_generator.num_rows_per_pass == 0:
            continue
        logger.debug('Generating data for table "%s".', table.name)
        # Run all the inserts for one table in a transaction
        with dst_conn.begin():
            for _ in range(table_generator.num_rows_per_pass):
                stmt = insert(table).values(table_generator(dst_conn))
                dst_conn.execute(stmt)
                row_counts[table.name] = row_counts.get(table.name, 0) + 1
    return row_counts
