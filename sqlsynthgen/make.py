"""Functions to make a module of generator classes."""
import asyncio
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final, Mapping, Optional, Sequence, Tuple
import yaml

import pandas as pd
import snsql
from black import FileMode, format_str
from jinja2 import Environment, FileSystemLoader, Template
from mimesis.providers.base import BaseProvider
from sqlalchemy import Engine, MetaData, UniqueConstraint, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import Column, Table
from sqlalchemy.sql import sqltypes, type_api

from sqlsynthgen import providers
from sqlsynthgen.settings import get_settings
from sqlsynthgen.utils import (
    create_db_engine,
    download_table,
    get_property,
    get_flag,
    get_related_table_names,
    get_sync_engine,
    get_vocabulary_table_names,
    logger,
)

from .serialize_metadata import metadata_to_dict

PROVIDER_IMPORTS: Final[list[str]] = []
for entry_name, entry in inspect.getmembers(providers, inspect.isclass):
    if issubclass(entry, BaseProvider) and entry.__module__ == "sqlsynthgen.providers":
        PROVIDER_IMPORTS.append(entry_name)

TEMPLATE_DIRECTORY: Final[Path] = Path(__file__).parent / "templates/"
SSG_TEMPLATE_FILENAME: Final[str] = "ssg.py.j2"


@dataclass
class VocabularyTableGeneratorInfo:
    """Contains the ssg.py content related to vocabulary tables."""

    variable_name: str
    table_name: str
    dictionary_entry: str


@dataclass
class FunctionCall:
    """Contains the ssg.py content related function calls."""

    function_name: str
    argument_values: list[str]


@dataclass
class RowGeneratorInfo:
    """Contains the ssg.py content related to row generators of a table."""

    variable_names: list[str]
    function_call: FunctionCall
    primary_key: bool = False


@dataclass
class TableGeneratorInfo:
    """Contains the ssg.py content related to regular tables."""

    class_name: str
    table_name: str
    columns: list[str]
    rows_per_pass: int
    row_gens: list[RowGeneratorInfo] = field(default_factory=list)
    unique_constraints: list[UniqueConstraint] = field(default_factory=list)


@dataclass
class StoryGeneratorInfo:
    """Contains the ssg.py content related to story generators."""

    wrapper_name: str
    function_call: FunctionCall
    num_stories_per_pass: int


def _get_function_call(
    function_name: str,
    positional_arguments: Optional[Sequence[Any]] = None,
    keyword_arguments: Optional[Mapping[str, Any]] = None,
) -> FunctionCall:
    if positional_arguments is None:
        positional_arguments = []

    if keyword_arguments is None:
        keyword_arguments = {}

    argument_values: list[str] = [str(value) for value in positional_arguments]
    argument_values += [f"{key}={value}" for key, value in keyword_arguments.items()]

    return FunctionCall(function_name=function_name, argument_values=argument_values)


def _get_row_generator(
    table_config: Mapping[str, Any],
) -> tuple[list[RowGeneratorInfo], list[str]]:
    """Get the row generators information, for the given table."""
    row_gen_info: list[RowGeneratorInfo] = []
    config: list[dict[str, Any]] = get_property(table_config, "row_generators", {})
    columns_covered = []
    for gen_conf in config:
        name: str = gen_conf["name"]
        columns_assigned = gen_conf["columns_assigned"]
        keyword_arguments: Mapping[str, Any] = gen_conf.get("kwargs", {})
        positional_arguments: Sequence[str] = gen_conf.get("args", [])

        if isinstance(columns_assigned, str):
            columns_assigned = [columns_assigned]

        variable_names: list[str] = columns_assigned
        try:
            columns_covered += columns_assigned
        except TypeError:
            # Might be a single string, rather than a list of strings.
            columns_covered.append(columns_assigned)

        row_gen_info.append(
            RowGeneratorInfo(
                variable_names=variable_names,
                function_call=_get_function_call(
                    name, positional_arguments, keyword_arguments
                ),
            )
        )
    return row_gen_info, columns_covered


def _get_default_generator(
    column: Column
) -> RowGeneratorInfo:
    """Get default generator information, for the given column."""
    # If it's a primary key column, we presume that primary keys are populated
    # automatically.

    # If it's a foreign key column, pull random values from the column it
    # references.
    variable_names: list[str] = []
    generator_function: str = ""
    generator_arguments: list[str] = []

    if column.foreign_keys:
        if len(column.foreign_keys) > 1:
            raise NotImplementedError(
                "Can't handle multiple foreign keys for one column."
            )
        fkey = next(iter(column.foreign_keys))
        target_name_parts = fkey.target_fullname.split(".")
        target_table_name = ".".join(target_name_parts[:-1])
        target_column_name = target_name_parts[-1]

        variable_names = [column.name]
        generator_function = "generic.column_value_provider.column_value"
        generator_arguments = [
            "dst_db_conn",
            f"metadata.tables['{target_table_name}']",
            f'"{target_column_name}"',
        ]
        return RowGeneratorInfo(
            primary_key=column.primary_key,
            variable_names=variable_names,
            function_call=_get_function_call(
                function_name=generator_function, positional_arguments=generator_arguments
            ),
        )

    # Otherwise generate values based on just the datatype of the column.
    (
        variable_names,
        generator_function,
        generator_arguments,
    ) = _get_provider_for_column(column)

    return RowGeneratorInfo(
        primary_key=column.primary_key,
        variable_names=variable_names,
        function_call=_get_function_call(
            function_name=generator_function, keyword_arguments=generator_arguments
        ),
    )


def _numeric_generator(column: Column) -> tuple[str, dict[str, str]]:
    """
    Returns the name of a generator and maybe arguments
    that limit its range to the permitted scale.
    """
    column_type = column.type
    if column_type.scale is None:
        return ("generic.numeric.float_number", {})
    return ("generic.numeric.float_number", {
        "start": 0,
        "end": 10 ** column_type.scale - 1,
    })


def _string_generator(column: Column) -> tuple[str, dict[str, str]]:
    """
    Returns the name of a string generator and maybe arguments
    that limit its length.
    """
    column_size: Optional[int] = getattr(column.type, "length", None)
    if column_size is None:
        return ("generic.text.color", {})
    return ("generic.person.password", { "length": str(column_size) })

def _integer_generator(column: Column) -> tuple[str, dict[str, str]]:
    """
    Returns the name of an integer generator.
    """
    if not column.primary_key:
        return ("generic.numeric.integer_number", {})
    return ("numeric.increment", {
        "accumulator": f'"{column.table.fullname}.{column.name}"'
    })

_COLUMN_TYPE_TO_GENERATOR = {
    sqltypes.Integer: "generic.numeric.integer_number",
    sqltypes.Boolean: "generic.development.boolean",
    sqltypes.Date: "generic.datetime.date",
    sqltypes.DateTime: "generic.datetime.datetime",
    sqltypes.Integer: _integer_generator,  # must be before Numeric
    sqltypes.Numeric: _numeric_generator,
    sqltypes.LargeBinary: "generic.bytes_provider.bytes",
    sqltypes.Uuid: "generic.cryptographic.uuid",
    postgresql.UUID: "generic.cryptographic.uuid",
    sqltypes.String: _string_generator,
}

def _get_generator_for_column(column_t: type) -> str | Callable[
    [type_api.TypeEngine], tuple[str, dict[str, str]]]:
    """
    Gets a generator from a column type.

    Returns either a string representing the callable, or a callable that,
    given the column.type will return a tuple (string representing generator
    callable, dict of keyword arguments to pass to the callable).
    """
    if column_t in _COLUMN_TYPE_TO_GENERATOR:
        return  _COLUMN_TYPE_TO_GENERATOR[column_t]

    # Search exhaustively for a superclass to the columns actual type
    for key, value in _COLUMN_TYPE_TO_GENERATOR.items():
        if issubclass(column_t, key):
            return value

    return None


def _get_generator_and_arguments(column: Column) -> tuple[str, dict[str, str]]:
    """
    Gets the generator and its arguments from the column type, returning
    a tuple of a string representing the generator callable and a dict of
    keyword arguments to supply to it.
    """
    generator_function = _get_generator_for_column(type(column.type))

    generator_arguments: dict[str, str] = {}
    if callable(generator_function):
        (generator_function, generator_arguments) = generator_function(column)
    return generator_function,generator_arguments


def _get_provider_for_column(column: Column) -> Tuple[list[str], str, dict[str, str]]:
    """
    Get a default Mimesis provider and its arguments for a SQL column type.

    Args:
        column: SQLAlchemy column object

    Returns:
        Tuple[str, str, list[str]]: Tuple containing the variable names to assign to,
        generator function and any generator arguments.
    """
    variable_names: list[str] = [column.name]

    generator_function, generator_arguments = _get_generator_and_arguments(column)

    # If we still don't have a generator, use null and warn.
    if not generator_function:
        generator_function = "generic.null_provider.null"
        logger.warning(
            "Unsupported SQLAlchemy type %s for column %s. "
            "Setting this column to NULL always, "
            "you may want to configure a row generator for it instead.",
            column.type,
            column.name,
        )

    return variable_names, generator_function, generator_arguments


def _constraint_sort_key(constraint: UniqueConstraint) -> str:
    """Extract a string out of a UniqueConstraint that is unique to that constraint.

    We sort the constraints so that the output of make_tables is deterministic, this is
    the sort key.
    """
    return (
        constraint.name
        if isinstance(constraint.name, str)
        else "_".join(map(str, constraint.columns))
    )


class _PrimaryConstraint:
    """
    Describes a Uniqueness constraint for when multiple
    columns in a table comprise the primary key. Not a
    real constraint, but enough to write ssg.py.
    """
    def __init__(self, *columns: Column, name: str):
        self.name = name
        self.columns = columns


def _get_generator_for_table(
    table_config: Mapping[str, Any], table: Table
) -> TableGeneratorInfo:
    """Get generator information for the given table."""
    unique_constraints = sorted(
        (
            constraint
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        ),
        key=_constraint_sort_key,
    )
    primary_keys = [
        c for c in table.columns
        if c.primary_key
    ]
    if 1 < len(primary_keys):
        unique_constraints.append(_PrimaryConstraint(
            *primary_keys,
            name=f"{table.name}_primary_key"
        ))
    table_data: TableGeneratorInfo = TableGeneratorInfo(
        table_name=table.name,
        class_name=table.name.title() + "Generator",
        columns=[str(col.name) for col in table.columns],
        rows_per_pass=get_property(table_config, "num_rows_per_pass", 1),
        unique_constraints=unique_constraints,
    )

    row_gen_info_data, columns_covered = _get_row_generator(table_config)
    table_data.row_gens.extend(row_gen_info_data)

    for column in table.columns:
        if column.name not in columns_covered:
            # No generator for this column in the user config.
            table_data.row_gens.append(_get_default_generator(column))

    return table_data


def _get_story_generators(config: Mapping) -> list[StoryGeneratorInfo]:
    """Get story generators."""
    generators = []
    for gen in config.get("story_generators", []):
        wrapper_name = "run_" + gen["name"].replace(".", "_").lower()
        generators.append(
            StoryGeneratorInfo(
                wrapper_name=wrapper_name,
                function_call=_get_function_call(
                    function_name=gen["name"],
                    keyword_arguments=gen.get("kwargs"),
                    positional_arguments=gen.get("args"),
                ),
                num_stories_per_pass=gen["num_stories_per_pass"],
            )
        )
    return generators


def make_vocabulary_tables(
    metadata: MetaData,
    config: Mapping,
    overwrite_files: bool,
):
    """
    Extracts the data from the source database for each
    vocabulary table.
    """
    settings = get_settings()
    src_dsn: str = settings.src_dsn or ""
    assert src_dsn != "", "Missing SRC_DSN setting."

    engine = get_sync_engine(create_db_engine(src_dsn, schema_name=settings.src_schema))
    vocab_names = get_vocabulary_table_names(config)
    for table_name in vocab_names:
        _generate_vocabulary_table(
            metadata.tables[table_name], engine, overwrite_files=overwrite_files
        )


def make_table_generators(  # pylint: disable=too-many-locals
    metadata: MetaData,
    config: Mapping,
    orm_filename: str,
    config_filename: str,
    src_stats_filename: Optional[str],
) -> str:
    """
    Create sqlsynthgen generator classes.

    The orm and vocabulary YAML files must already have been
    generated (by make-tables and make-vocab).

    Args:
      config: Configuration to control the generator creation.
      src_stats_filename: A filename for where to read src stats from.
        Optional, if `None` this feature will be skipped
      overwrite_files: Whether to overwrite pre-existing vocabulary files

    Returns:
      A string that is a valid Python module, once written to file.
    """
    row_generator_module_name: str = config.get("row_generators_module", None)
    story_generator_module_name = config.get("story_generators_module", None)

    settings = get_settings()
    src_dsn: str = settings.src_dsn or ""
    assert src_dsn != "", "Missing SRC_DSN setting."

    tables_config = config.get("tables", {})
    engine = get_sync_engine(create_db_engine(src_dsn, schema_name=settings.src_schema))

    tables: list[TableGeneratorInfo] = []
    vocabulary_tables: list[VocabularyTableGeneratorInfo] = []
    vocab_names = get_vocabulary_table_names(config)
    for (table_name, table) in metadata.tables.items():
        if table_name in vocab_names:
            related = get_related_table_names(table)
            related_non_vocab = related.difference(vocab_names)
            if related_non_vocab:
                logger.warning(
                    "Making table '%s' a vocabulary table requires that also the"
                    " related tables (%s) be also vocabulary tables.",
                    table.name,
                    related_non_vocab
                )
            vocabulary_tables.append(
                _get_generator_for_existing_vocabulary_table(
                    table, engine
                )
            )
        else:
            tables.append(_get_generator_for_table(
                tables_config.get(table.name, {}),
                table
            ))

    story_generators = _get_story_generators(config)

    max_unique_constraint_tries = config.get("max-unique-constraint-tries", None)
    return generate_ssg_content(
        {
            "provider_imports": PROVIDER_IMPORTS,
            "orm_file_name": orm_filename,
            "config_file_name": repr(config_filename),
            "row_generator_module_name": row_generator_module_name,
            "story_generator_module_name": story_generator_module_name,
            "src_stats_filename": src_stats_filename,
            "tables": tables,
            "vocabulary_tables": vocabulary_tables,
            "story_generators": story_generators,
            "max_unique_constraint_tries": max_unique_constraint_tries,
        }
    )


def generate_ssg_content(template_context: Mapping[str, Any]) -> str:
    """Generate the content of the ssg.py file as a string."""
    environment: Environment = Environment(
        loader=FileSystemLoader(TEMPLATE_DIRECTORY),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    ssg_template: Template = environment.get_template(SSG_TEMPLATE_FILENAME)
    template_output: str = ssg_template.render(template_context)

    return format_str(template_output, mode=FileMode())


def _get_generator_for_existing_vocabulary_table(
    table: Table,
    engine: Engine,
    table_file_name: Optional[str] = None,
) -> VocabularyTableGeneratorInfo:
    """
    Turns an existing vocabulary YAML file into a VocabularyTableGeneratorInfo.
    """
    yaml_file_name: str = table_file_name or table.fullname + ".yaml"
    if not Path(yaml_file_name).exists():
        logger.error("%s has not already been generated, please run make-vocab first", yaml_file_name)
        sys.exit(1)
    logger.debug("Downloading vocabulary table %s", table.name)
    download_table(table, engine, yaml_file_name)
    logger.debug("Done downloading %s", table.name)

    return VocabularyTableGeneratorInfo(
        dictionary_entry=table.name,
        variable_name=f"{table.name.lower()}_vocab",
        table_name=table.name,
    )


def _generate_vocabulary_table(
    table: Table,
    engine: Engine,
    overwrite_files: bool = False,
):
    """
    Pulls data out of the source database to make a vocabulary YAML file
    """
    yaml_file_name: str = table.fullname + ".yaml"
    if Path(yaml_file_name).exists() and not overwrite_files:
        logger.debug("%s already exists; not overwriting", yaml_file_name)
        return
    logger.debug("Downloading vocabulary table %s", table.name)
    download_table(table, engine, yaml_file_name)


def make_tables_file(
    db_dsn: str, schema_name: Optional[str], config: Mapping[str, Any]
) -> str:
    """
    Construct the YAML file representing the schema.
    """
    tables_config = config.get("tables", {})
    engine = get_sync_engine(create_db_engine(db_dsn, schema_name=schema_name))

    def reflect_if(table_name: str, _: Any) -> bool:
        table_config = tables_config.get(table_name, {})
        ignore = get_flag(table_config, "ignore")
        return not ignore

    metadata = MetaData()
    metadata.reflect(
        engine,
        only=reflect_if,
    )
    meta_dict = metadata_to_dict(metadata, db_dsn, engine.dialect)

#    for table_name in metadata.tables.keys():
#        table_config = tables_config.get(table_name, {})
#        ignore = get_flag(table_config, "ignore")
#        if ignore:
#            logger.warning(
#                "Table %s is supposed to be ignored but there is a foreign key "
#                "reference to it. "
#                "You may need to create this table manually at the dst schema before "
#                "running create-tables.",
#                table_name,
#            )

    return yaml.dump(meta_dict)


async def make_src_stats(
    dsn: str, config: Mapping, schema_name: Optional[str] = None
) -> dict[str, list[dict]]:
    """Run the src-stats queries specified by the configuration.

    Query the src database with the queries in the src-stats block of the `config`
    dictionary, using the differential privacy parameters set in the `smartnoise-sql`
    block of `config`. Record the results in a dictionary and returns it.
    Args:
        dsn: database connection string
        config: a dictionary with the necessary configuration
        schema_name: name of the database schema

    Returns:
        The dictionary of src-stats.
    """
    use_asyncio = config.get("use-asyncio", False)
    engine = create_db_engine(dsn, schema_name=schema_name, use_asyncio=use_asyncio)

    async def execute_query(query_block: Mapping[str, Any]) -> Any:
        """Execute query in query_block."""
        logger.debug("Executing query %s", query_block["name"])
        query = text(query_block["query"])
        if isinstance(engine, AsyncEngine):
            async with engine.connect() as conn:
                raw_result = await conn.execute(query)
        else:
            with engine.connect() as conn:
                raw_result = conn.execute(query)

        if "dp-query" in query_block:
            result_df = pd.DataFrame(raw_result.mappings())
            logger.debug("Executing dp-query for %s", query_block["name"])
            dp_query = query_block["dp-query"]
            snsql_metadata = {"": {"": {"query_result": query_block["snsql-metadata"]}}}
            privacy = snsql.Privacy(
                epsilon=query_block["epsilon"], delta=query_block["delta"]
            )
            reader = snsql.from_df(result_df, privacy=privacy, metadata=snsql_metadata)
            private_result = reader.execute(dp_query)
            header = tuple(str(x) for x in private_result[0])
            final_result = [dict(zip(header, row)) for row in private_result[1:]]
        else:
            final_result = [
                {str(k): v for k, v in row.items()}
                for row in raw_result.mappings().fetchall()
            ]
        return final_result

    query_blocks = config.get("src-stats", [])
    results = await asyncio.gather(
        *[execute_query(query_block) for query_block in query_blocks]
    )
    src_stats = {
        query_block["name"]: result
        for query_block, result in zip(query_blocks, results)
    }

    for name, result in src_stats.items():
        if not result:
            logger.warning("src-stats query %s returned no results", name)
    return src_stats
