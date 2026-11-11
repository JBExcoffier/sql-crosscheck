#!/usr/bin/env python3
"""
Database Schema Generator for PostgreSQL

This script connects to a PostgreSQL database using environment variables
and generates a structured YAML file containing database schema information.
"""

import os
import sys
import yaml
from psycopg2.extras import RealDictCursor
from typing import Dict, List, Any
import logging

from database.connection import DatabaseConnection

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_improved_schema_query():
    """Return an improved SQL query for extracting database schema information."""
    return """
    WITH table_info AS (
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            c.oid AS table_oid,
            COALESCE(obj_description(c.oid), '') AS table_comment,
            (SELECT COUNT(*) FROM information_schema.columns 
             WHERE table_schema = n.nspname AND table_name = c.relname) AS column_count
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relkind = 'r'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    ),
    primary_keys AS (
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            string_agg(att.attname, ', ' ORDER BY att.attnum) AS pk_columns
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute att ON att.attrelid = i.indrelid AND att.attnum = ANY(i.indkey)
        WHERE i.indisprimary = true
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        GROUP BY n.nspname, c.relname
    ),
    foreign_keys AS (
        SELECT
            n.nspname AS schema_name,
            c.relname AS table_name,
            string_agg(att.attname, ', ' ORDER BY att.attnum) AS fk_columns
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f'
          AND n.nspname NOT IN ('pg_catalog', 'information_schema')
        GROUP BY n.nspname, c.relname
    ),
    columns_info AS (
        SELECT
            table_schema,
            table_name,
            json_agg(json_build_object('name', column_name, 'type', data_type) ORDER BY ordinal_position) AS columns
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        GROUP BY table_schema, table_name
    )
    SELECT
        ti.schema_name,
        ti.table_name,
        ti.column_count,
        ti.table_comment,
        COALESCE(pk.pk_columns, '') AS primary_keys,
        COALESCE(fk.fk_columns, '') AS foreign_keys,
        ci.columns
    FROM table_info ti
    LEFT JOIN primary_keys pk ON pk.schema_name = ti.schema_name AND pk.table_name = ti.table_name
    LEFT JOIN foreign_keys fk ON fk.schema_name = ti.schema_name AND fk.table_name = ti.table_name
    LEFT JOIN columns_info ci ON ci.table_schema = ti.schema_name AND ci.table_name = ti.table_name
    ORDER BY ti.schema_name, ti.table_name;
    """


def get_table_row_count(connection, schema_name: str, table_name: str) -> int:
    """Get the row count for a specific table."""
    try:
        with connection.cursor() as cursor:
            query = f'SELECT COUNT(*) FROM "{schema_name}"."{table_name}"'
            logger.debug(f"Executing row count query: {query}")
            cursor.execute(query)
            result = cursor.fetchone()
            row_count = result[0] if result else 0
            logger.debug(f"Row count for {schema_name}.{table_name}: {row_count}")
            return row_count
    except Exception as e:
        # Permission issues
        if (
            "permission denied" in str(e).lower()
            or "insufficient privilege" in str(e).lower()
        ):
            raise PermissionError(
                f"Insufficient permissions to access table {schema_name}.{table_name}. "
                f"Error: {e}. Please ensure the database user has SELECT privileges on this table."
            )
        else:
            raise RuntimeError(
                f"Failed to get row count for table {schema_name}.{table_name}: {e}"
            )


def parse_column_list(column_string: str) -> List[str]:
    """Parse comma-separated column string into a list."""
    if not column_string or column_string.strip() == "":
        return []
    return [col.strip() for col in column_string.split(",")]


def generate_schema_yaml(connection) -> Dict[str, Any]:
    """Generate the complete database schema structure as a dictionary."""
    schema_data = {}

    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(get_improved_schema_query())
            tables = cursor.fetchall()

            for table in tables:
                schema_name = table["schema_name"]
                table_name = table["table_name"]

                row_count = get_table_row_count(connection, schema_name, table_name)

                primary_keys = parse_column_list(table["primary_keys"])
                foreign_keys = parse_column_list(table["foreign_keys"])
                columns = table.get("columns", [])

                if schema_name not in schema_data:
                    schema_data[schema_name] = {}

                schema_data[schema_name][table_name] = {
                    "comment": table["table_comment"] or "",
                    "number_of_columns": table["column_count"],
                    "columns": columns,
                    "list_of_primary_keys": primary_keys,
                    "list_of_foreign_keys": foreign_keys,
                    "number_of_rows": row_count,
                }

                logger.info(f"Processed table: {schema_name}.{table_name}")

    except Exception as e:
        if isinstance(e, PermissionError):
            logger.error(f"Permission error in schema generation: {e}")
            raise
        else:
            logger.error(f"Error generating schema data: {e}")
            raise

    return schema_data


def save_yaml_file(schema_data: Dict[str, Any], output_path: str):
    """Save the schema data to a YAML file."""
    try:
        with open(output_path, "w", encoding="utf-8") as yaml_file:
            yaml.dump(
                schema_data,
                yaml_file,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                indent=2,
            )

        logger.info(f"Schema YAML file saved to: {output_path}")

    except Exception as e:
        logger.error(f"Error saving YAML file: {e}")
        raise


def main():
    """Main function to generate database schema YAML."""
    try:
        logger.info("Starting database schema generation...")

        with DatabaseConnection() as db_conn:
            db_name = db_conn.get_database_name()
            logger.info(f"Target database: {db_name}")

            # Checking information_schema access : https://www.postgresql.org/docs/current/information-schema.html
            logger.info("Verifying database permissions...")
            try:
                with db_conn.get_connection().cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM information_schema.tables LIMIT 1")
                    cursor.fetchone()
                logger.info("information_schema permissions verified")
            except Exception as e:
                raise PermissionError(
                    f"Cannot access information_schema. This indicates insufficient database permissions. "
                    f"Error: {e}. Please ensure the user has SELECT privileges on information_schema."
                )

            schema_data = generate_schema_yaml(db_conn.get_connection())

        output_dir = os.path.dirname(os.path.abspath(__file__))
        db_folder = os.path.join(output_dir, "..", "databases", db_name)
        output_path = os.path.join(db_folder, "schema.yaml")

        logger.info(f"Creating output directory: {db_folder}")
        os.makedirs(db_folder, exist_ok=True)

        # Saving YAML file
        save_yaml_file(schema_data, output_path)

        # Printing logs and schema summary
        total_schemas = len(schema_data)
        total_tables = sum(len(tables) for tables in schema_data.values())

        logger.info(f"Schema generation completed successfully!")
        logger.info(f"Total schemas: {total_schemas}")
        logger.info(f"Total tables: {total_tables}")
        logger.info(f"Output file: {output_path}")

        for schema_name, tables in schema_data.items():
            logger.info(f"  {schema_name}: {len(tables)} tables")

    except PermissionError as e:
        logger.error(f"Schema generation failed due to insufficient permissions:")
        logger.error(f"  {e}")
        logger.error("")
        logger.error("To resolve this issue:")
        logger.error(
            "  1. Ensure the database user has SELECT privileges on all tables"
        )
        logger.error("  2. Check if the user has access to the information_schema")
        logger.error("  3. Verify the user can access the target schemas")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Schema generation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
