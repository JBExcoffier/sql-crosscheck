import os


def get_schema_as_str(db_name: str):
    schema_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "documentation",
        "databases",
        db_name,
        "schema.yaml",
    )
    schema_path = os.path.abspath(schema_path)
    if not os.path.isfile(schema_path):
        raise FileNotFoundError(
            f"Schema file not found for database '{db_name}': {schema_path}"
        )
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()
        return schema
