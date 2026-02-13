"""Extensions of SQLAlchemy."""
from typing import Any, Union

from sqlalchemy.ext.compiler import compiles, SQLCompiler
from sqlalchemy.sql.elements import ColumnClause, TextClause, ColumnElement
from sqlalchemy.sql.functions import GenericFunction
from sqlalchemy.types import JSON

class JsonObjectAggregate(GenericFunction):
    """Aggregation of columns into array of objects."""
    type = JSON()
    name = "json_object_aggregate"
    clauses: Any

    def __init__(self, *args: Union[ColumnClause, TextClause]) -> None:
        """Initialise JsonObjectAggregate."""
        super().__init__(*args)


def json_object_aggregate(template: str, element: JsonObjectAggregate, compiler: SQLCompiler, **kw: dict[str, Any]) -> str:
    """
    :param template: String with interpolations ``{jo_args}`` as a list of
        alternating key names and expressions, and ``{exprs}`` as a list
        of exprs.
    :param element: The JsonObjectAggregate being compiled.
    :param compiler: The compiler.
    :param kw: Further arguments.
    :return: The SQL.
    """
    elements = {
        ec.key: compiler.process(ec, **kw)
        for ec in element.clause_expr.element.clauses
    }
    jo_args = ", ".join(f"{k}, {v}" for k, v in elements.items())
    exprs = ", ".join(elements.values())
    return template.format(jo_args=jo_args, exprs=exprs)


@compiles(JsonObjectAggregate, "duckdb")
def json_object_aggregate_duckdb(element: JsonObjectAggregate, compiler: SQLCompiler, **kw: dict[str, Any]) -> str:
    """
    :param element: The JsonObjectAggregate being compiled.
    :param compiler: The compiler.
    :param kw: Further arguments.
    :return: The SQL.
    """
    return json_object_aggregate(
        "LIST(JSON_OBJECT({jo_args}) ORDER BY {exprs})",
        element, compiler, kw,
    )


@compiles(JsonObjectAggregate, "postgresql")
def json_object_aggregate_postgresql(element: JsonObjectAggregate, compiler: SQLCompiler, **kw: dict[str, Any]) -> str:
    """
    :param element: The JsonObjectAggregate being compiled.
    :param compiler: The compiler.
    :param kw: Further arguments.
    :return: The SQL.
    """
    return json_object_aggregate(
        "JSON_AGG(JSON_BUILD_OBJECT({jo_args}) ORDER BY {exprs})",
        element, compiler, kw,
    )
