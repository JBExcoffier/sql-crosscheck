from unittest.mock import MagicMock

import pytest

from database.connection import DatabaseConnection
from documentation.auto_doc import generate_database_schema as schema


@pytest.fixture
def database(monkeypatch):
    db = DatabaseConnection()
    connection = MagicMock()
    connection.closed = False
    connection.get_dsn_parameters.return_value = {"dbname": "adventureworks"}
    db.connection = connection
    monkeypatch.setattr(db, "connect", MagicMock(return_value=db))
    monkeypatch.setattr(schema, "DatabaseConnection", lambda: db)
    monkeypatch.setattr(schema, "generate_schema_yaml", MagicMock(return_value={
        "humanresources": {"employee": {}}
    }))
    monkeypatch.setattr(schema, "save_yaml_file", MagicMock())
    return db, connection


def test_schema_generation_closes_connection_before_saving(database):
    db, connection = database

    def save(data, path):
        assert data == {"humanresources": {"employee": {}}}
        assert path.endswith("adventureworks/schema.yaml")
        assert db.connection is None
        connection.close.assert_called_once()

    schema.save_yaml_file.side_effect = save
    schema.main()
    schema.save_yaml_file.assert_called_once()
    assert db.connection is None


@pytest.mark.parametrize("failure", ["permissions", "schema", "schema_permissions"])
def test_schema_generation_failure_always_closes_connection(database, failure):
    db, connection = database
    if failure == "permissions":
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = PermissionError("No metadata access")
    else:
        error = PermissionError if failure == "schema_permissions" else RuntimeError
        schema.generate_schema_yaml.side_effect = error("Schema generation failed")

    with pytest.raises(SystemExit) as exc:
        schema.main()
    assert exc.value.code == 1
    assert db.connection is None
    connection.close.assert_called_once()
    schema.save_yaml_file.assert_not_called()
