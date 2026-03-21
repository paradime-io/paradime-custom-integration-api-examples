"""Parser for Snowflake Notebook (.ipynb) files."""

from .parser import extract_tables_from_sql, parse_notebook_file

__all__ = ["extract_tables_from_sql", "parse_notebook_file"]
