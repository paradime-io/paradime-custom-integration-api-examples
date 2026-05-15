"""SQL Table Tracker - Parse SQL and track table usage."""

import re
import ast
from typing import List, Dict, Set, Optional
from pathlib import Path

import duckdb
import polars as pl
import sqlglot
from sqlglot import parse_one, exp


class SQLTableTracker:
    """
    Parse SQL statements from Python files and track table usage.
    
    This class extracts SQL queries from Python code, parses them using sqlglot,
    and stores table dependencies in a DuckDB database.
    """

    def __init__(self, db_path: str = "table_usage.duckdb"):
        """
        Initialize the SQL Table Tracker.
        
        Args:
            db_path: Path to DuckDB database file
        """
        self.db_path = db_path
        self.conn = duckdb.connect(db_path)
        self._create_schema()

    def _create_schema(self):
        """Create the DuckDB schema for storing table usage."""
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_table_usage START 1")
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_app_metadata START 1")

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS table_usage (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_table_usage'),
                file_path VARCHAR,
                chart_type VARCHAR,
                chart_name VARCHAR,
                chart_caption VARCHAR,
                line_number INTEGER,
                sql_query TEXT,
                tables_used VARCHAR[],
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS app_metadata (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_app_metadata'),
                file_path VARCHAR,
                app_title VARCHAR,
                app_description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    def extract_sql_from_file(self, file_path: str) -> List[Dict]:
        """
        Extract SQL queries from a Python file.
        
        Args:
            file_path: Path to the Python file
            
        Returns:
            List of dictionaries containing SQL queries and metadata
        """
        with open(file_path, 'r') as f:
            content = f.read()

        sql_queries = []
        seen_sql: Set[str] = set()

        def _add(sql_text: str, line_number: int | None) -> None:
            key = sql_text.strip()
            if key and key not in seen_sql and self._looks_like_sql(key):
                seen_sql.add(key)
                sql_queries.append({'sql': key, 'line_number': line_number})

        # Parse the Python AST
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            print(f"Error parsing {file_path}: {e}")
            return sql_queries

        # Find all string literals that look like SQL.
        # Walk manually so we can intercept f-strings (ast.JoinedStr) and
        # reconstruct them as a single string before their children are
        # visited individually — otherwise each literal chunk between
        # placeholders is treated as its own string, which splits a SQL
        # statement mid-clause (e.g. "...FROM " and ".dbt_tableau_metrics..."
        # become separate strings and the table name is lost).
        def _visit(node: ast.AST) -> None:
            if isinstance(node, ast.JoinedStr):
                parts: list[str] = []
                for value in node.values:
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        parts.append(value.value)
                    elif isinstance(value, ast.FormattedValue):
                        # Preserve a {…} placeholder so extract_tables_from_sql's
                        # pre-processing step can substitute it before parsing.
                        parts.append("{x}")
                _add("".join(parts), getattr(node, "lineno", None))
                return  # do not descend into the literal chunks
            if isinstance(node, ast.Str):
                _add(node.s, getattr(node, "lineno", None))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                _add(node.value, getattr(node, "lineno", None))
            for child in ast.iter_child_nodes(node):
                _visit(child)

        _visit(tree)

        # Regex fallback to catch f-strings and session.sql() patterns
        sql_pattern = r'session\.sql\s*\(\s*[f]?["\']+(.*?)["\']+'
        for match in re.finditer(sql_pattern, content, re.DOTALL | re.IGNORECASE):
            line_number = content[:match.start()].count('\n') + 1
            _add(match.group(1), line_number)

        return sql_queries

    def _looks_like_sql(self, text: str) -> bool:
        """
        Check if a string looks like SQL.

        Args:
            text: String to check

        Returns:
            True if the string looks like SQL
        """
        if len(text.strip()) < 15:
            return False

        text_upper = text.upper().strip()

        # Accept plain SELECT queries and CTEs (WITH ... AS ( SELECT ...))
        is_select = text_upper.startswith('SELECT')
        is_cte = text_upper.startswith('WITH') and 'SELECT' in text_upper
        if not (is_select or is_cte):
            return False

        if 'FROM' not in text_upper:
            return False

        english_indicators = [
            'THE ', 'A ', 'AN ', 'IS ', 'ARE ', 'WAS ', 'WERE ',
            'THIS ', 'THAT ', 'THESE ', 'THOSE ', 'WILL ', 'CAN '
        ]
        english_count = sum(
            1 for indicator in english_indicators
            if f' {indicator}' in f' {text_upper}' or text_upper.startswith(indicator)
        )
        if english_count > 2:
            return False

        return True

    def extract_tables_from_sql(self, sql: str, dialect: str = "snowflake") -> Set[str]:
        """
        Extract table names from SQL query using sqlglot.
        
        Args:
            sql: SQL query string
            dialect: SQL dialect (default: snowflake)
            
        Returns:
            Set of fully qualified table names
        """
        tables = set()
        
        try:
            # Pre-process SQL to handle f-string placeholders.
            # Substitute {variable} with a valid SQL identifier so sqlglot
            # treats the surrounding `db.schema.table` chain as identifiers
            # rather than a numeric literal (replacing with '1' yields
            # `1.1.table` which tokenises as the number 1.1).
            processed_sql = re.sub(r'\{[^}]+\}', 'placeholder', sql)
            
            # Parse the SQL
            parsed = parse_one(processed_sql, dialect=dialect)
            
            # Find all table references
            for table in parsed.find_all(exp.Table):
                table_parts = []
                
                if table.catalog:
                    table_parts.append(table.catalog)
                if table.db:
                    table_parts.append(table.db)
                if table.name:
                    table_parts.append(table.name)
                
                if table_parts:
                    full_table_name = ".".join(table_parts)
                    tables.add(full_table_name)
                    
        except Exception as e:
            print(f"Error parsing SQL: {e}")
            print(f"SQL: {sql[:100]}...")
            
        return tables

    def infer_chart_info(self, file_content: str, line_number: int) -> tuple:
        """
        Infer chart type, name, and caption from surrounding code context.
        
        Args:
            file_content: Full file content
            line_number: Line number where SQL was found
            
        Returns:
            Tuple of (chart_type, chart_name, chart_caption)
        """
        lines = file_content.split('\n')
        
        # First, find where the chart is actually rendered (after the SQL query)
        # Look ahead from SQL line to find chart rendering
        chart_render_line = None
        chart_type = "unknown"
        
        # Search forward from SQL line (up to 50 lines) to find chart rendering
        search_end = min(len(lines), line_number + 50)
        for i in range(line_number, search_end):
            line = lines[i]
            if "st.bar_chart" in line:
                chart_type = "bar_chart"
                chart_render_line = i
                break
            elif "st.line_chart" in line:
                chart_type = "line_chart"
                chart_render_line = i
                break
            elif "st.dataframe" in line:
                chart_type = "dataframe"
                chart_render_line = i
                break
            elif "st.table" in line:
                chart_type = "table"
                chart_render_line = i
                break
            elif "st.metric" in line:
                chart_type = "metric"
                chart_render_line = i
                break
            elif "to_pandas()" in line:
                chart_type = "pandas_dataframe"
                chart_render_line = i
                break
        
        # If no chart render found, fallback to SQL line
        if chart_render_line is None:
            chart_render_line = line_number
        
        # Now search backwards from the chart render line to find the most recent header
        # This way, it doesn't matter how long the SQL query is
        chart_name = None
        chart_caption = None
        
        # Search backwards from chart render line to find the nearest header
        for i in range(chart_render_line, -1, -1):
            line = lines[i]
            
            # Check for various header types (stop at first found)
            subheader_match = re.search(r'st\.subheader\(["\']([^"\']+)["\']\)', line)
            if subheader_match:
                chart_name = subheader_match.group(1)
                break
            
            header_match = re.search(r'st\.header\(["\']([^"\']+)["\']\)', line)
            if header_match:
                chart_name = header_match.group(1)
                break
            
            # Check for tab context
            tab_match = re.search(r'with\s+(\w+)\[.*?\]', line)
            if tab_match:
                # Try to find the tab name from the tab definition
                tab_var = tab_match.group(1)
                # Look back to find where tabs were defined
                for j in range(i, max(0, i - 30), -1):
                    tabs_def = re.search(rf'{tab_var}\s*=\s*st\.tabs\(\[(.*?)\]', lines[j])
                    if tabs_def:
                        # Extract tab names
                        tab_names_str = tabs_def.group(1)
                        tab_names = re.findall(r'["\']([^"\']+)["\']', tab_names_str)
                        if tab_names:
                            # Use first tab name or try to match context
                            chart_name = tab_names[0]
                            break
                break
        
        # Search near chart render line for caption (within 10 lines after)
        caption_search_end = min(len(lines), chart_render_line + 10)
        for i in range(chart_render_line, caption_search_end):
            caption_match = re.search(r'st\.caption\(["\']([^"\']+)["\']\)', lines[i])
            if caption_match:
                chart_caption = caption_match.group(1)
                break
                
        return chart_type, chart_name, chart_caption

    def extract_app_metadata(self, file_content: str) -> tuple:
        """
        Extract app-level metadata like title and description.
        
        Args:
            file_content: Full file content
            
        Returns:
            Tuple of (app_title, app_description)
        """
        app_title = None
        app_description = None
        
        # Extract st.title
        title_match = re.search(r'st\.title\(["\']([^"\']+)["\']\)', file_content)
        if title_match:
            app_title = title_match.group(1)
        
        # Extract st.markdown (typically used for app description)
        # Look for markdown right after title
        markdown_pattern = r'st\.markdown\(["\'"]{3}(.*?)["\'"]{3}\)'
        markdown_match = re.search(markdown_pattern, file_content, re.DOTALL)
        if markdown_match:
            app_description = markdown_match.group(1).strip()
        
        return app_title, app_description
    
    def parse_file(self, file_path: str):
        """
        Parse a Python file and store table usage in the database.
        
        Args:
            file_path: Path to the Python file to parse
        """
        # Read file content
        with open(file_path, 'r') as f:
            file_content = f.read()
        
        # Extract app metadata
        app_title, app_description = self.extract_app_metadata(file_content)
        
        # Store app metadata
        if app_title or app_description:
            try:
                self.conn.execute("""
                    INSERT INTO app_metadata (file_path, app_title, app_description)
                    VALUES (?, ?, ?)
                """, (
                    str(file_path),
                    app_title,
                    app_description,
                ))
            except Exception as e:
                print(f"Warning: could not store app metadata for {file_path}: {e}")
            print(f"App Metadata:")
            print(f"  Title: {app_title or 'Not found'}")
            print(f"  Description: {app_description[:100] if app_description else 'Not found'}...")
        
        # Extract SQL queries
        sql_queries = self.extract_sql_from_file(file_path)
        
        print(f"\nFound {len(sql_queries)} SQL queries in {file_path}")
        
        # Process each query
        for idx, query_info in enumerate(sql_queries):
            sql = query_info['sql']
            line_number = query_info['line_number']
            
            # Skip very short queries
            if len(sql.strip()) < 15:
                continue
            
            # Extract tables
            try:
                tables = self.extract_tables_from_sql(sql)
            except Exception as e:
                print(f"  Warning: could not extract tables from query at line {line_number}: {e}")
                tables = set()

            # Infer chart information
            try:
                chart_type, chart_name, chart_caption = self.infer_chart_info(file_content, line_number or 0)
            except Exception as e:
                print(f"  Warning: could not infer chart info at line {line_number}: {e}")
                chart_type, chart_name, chart_caption = "unknown", None, None

            # Store in database
            try:
                self.conn.execute("""
                    INSERT INTO table_usage (file_path, chart_type, chart_name, chart_caption, line_number, sql_query, tables_used)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(file_path),
                    chart_type,
                    chart_name,
                    chart_caption,
                    line_number,
                    sql,
                    list(tables),
                ))
            except Exception as e:
                print(f"  Warning: could not store query at line {line_number}: {e}")
                continue

            print(f"  [{idx+1}] {chart_type}: {chart_name or 'Unnamed'}")
            print(f"      Caption: {chart_caption or 'None'}")
            print(f"      Tables: {', '.join(tables) or 'None'}")

    def get_results(self) -> pl.DataFrame:
        """
        Get all tracked table usage as a Polars DataFrame.
        
        Returns:
            Polars DataFrame with table usage information
        """
        query = """
            SELECT 
                id,
                file_path,
                chart_type,
                chart_name,
                chart_caption,
                line_number,
                sql_query,
                tables_used,
                created_at
            FROM table_usage
            ORDER BY id
        """
        
        result = self.conn.execute(query).fetch_arrow_table()
        return pl.from_arrow(result)
    
    def get_app_metadata(self) -> Optional[Dict]:
        """
        Get app metadata from the database.
        
        Returns:
            Dictionary with app_title and app_description or None
        """
        query = """
            SELECT 
                app_title,
                app_description
            FROM app_metadata
            LIMIT 1
        """
        
        result = self.conn.execute(query).fetchone()
        if result:
            return {
                'app_title': result[0],
                'app_description': result[1]
            }
        return None

    def get_table_summary(self) -> pl.DataFrame:
        """
        Get a summary of table usage across all charts.
        
        Returns:
            Polars DataFrame with table usage summary
        """
        query = """
            WITH unnested AS (
                SELECT 
                    UNNEST(tables_used) AS table_name,
                    chart_type,
                    chart_name
                FROM table_usage
            )
            SELECT 
                table_name,
                COUNT(*) AS usage_count,
                LIST(DISTINCT chart_type) AS used_in_chart_types,
                LIST(DISTINCT chart_name) AS used_in_charts
            FROM unnested
            WHERE table_name IS NOT NULL AND table_name != ''
            GROUP BY table_name
            ORDER BY usage_count DESC
        """
        
        result = self.conn.execute(query).fetch_arrow_table()
        return pl.from_arrow(result)

    def close(self):
        """Close the database connection."""
        self.conn.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
