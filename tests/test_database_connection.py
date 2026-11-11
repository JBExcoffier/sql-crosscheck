"""
Tests for database connection and environment configuration.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from database.connection import DatabaseConnection


class TestDatabaseConnection:
    """Test DatabaseConnection class functionality."""

    def setup_method(self):
        """Set up test environment variables before each test."""
        self.test_env_vars = {
            "POSTGRES_DB_USER": "test_user",
            "POSTGRES_DB_PASSWORD": "test_password",
            "POSTGRES_DB_NAME": "test_db",
            "POSTGRES_DB_HOST": "localhost",
            "POSTGRES_DB_PORT": "5432",
        }

    def test_database_connection_initialization(self):
        """Test that DatabaseConnection initializes correctly."""
        db_conn = DatabaseConnection()
        assert db_conn.connection is None
        assert db_conn.db_name is None
        assert db_conn.db_user is None
        assert db_conn.db_host is None
        assert db_conn.db_port is None

    @patch("psycopg2.connect")
    def test_successful_connection(self, mock_connect):
        """Test successful database connection."""
        # Mock the psycopg2 connection
        mock_connection = MagicMock()
        mock_connection.get_dsn_parameters.return_value = {"dbname": "test_db"}
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            result = db_conn.connect()

            assert result is db_conn
            assert db_conn.connection is mock_connection
            assert db_conn.db_name == "test_db"
            assert db_conn.db_user == "test_user"
            assert db_conn.db_host == "localhost"
            assert db_conn.db_port == "5432"

            # Checking if psycopg2.connect was called with correct parameters
            mock_connect.assert_called_once_with(
                host="localhost",
                port="5432",
                database="test_db",
                user="test_user",
                password="test_password",
            )

    @patch("psycopg2.connect")
    def test_connection_failure(self, mock_connect):
        """Test database connection failure."""
        mock_connect.side_effect = Exception("Connection failed")

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            with pytest.raises(Exception, match="Connection failed"):
                db_conn.connect()

    def test_get_database_name_without_connection(self):
        """Test that get_database_name fails without an active connection."""
        db_conn = DatabaseConnection()
        with pytest.raises(RuntimeError, match="No active database connection"):
            db_conn.get_database_name()

    def test_get_connection_without_connection(self):
        """Test that get_connection fails without an active connection."""
        db_conn = DatabaseConnection()
        with pytest.raises(RuntimeError, match="No active database connection"):
            db_conn.get_connection()

    @patch("psycopg2.connect")
    def test_get_database_name_success(self, mock_connect):
        """Test successful retrieval of database name."""
        mock_connection = MagicMock()
        mock_connection.get_dsn_parameters.return_value = {"dbname": "test_db"}
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            db_conn.connect()
            db_name = db_conn.get_database_name()
            assert db_name == "test_db"

    @patch("psycopg2.connect")
    def test_get_database_name_missing_dbname(self, mock_connect):
        """Test that get_database_name fails when dbname is missing from DSN."""
        mock_connection = MagicMock()
        mock_connection.get_dsn_parameters.return_value = {}
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            db_conn.connect()
            with pytest.raises(ValueError, match="Database name not found"):
                db_conn.get_database_name()

    @patch("psycopg2.connect")
    def test_get_database_name_dsn_error(self, mock_connect):
        """Test that get_database_name handles DSN parameter errors."""
        mock_connection = MagicMock()
        mock_connection.get_dsn_parameters.side_effect = Exception("DSN error")
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            db_conn.connect()
            with pytest.raises(ValueError, match="Could not retrieve database name"):
                db_conn.get_database_name()

    @patch("psycopg2.connect")
    def test_is_connected(self, mock_connect):
        """Test connection status checking."""
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()

            # Initially not connected
            assert not db_conn.is_connected()

            # After connection
            db_conn.connect()
            assert db_conn.is_connected()

            # After closing
            db_conn.close()
            assert not db_conn.is_connected()

    @patch("psycopg2.connect")
    def test_close_connection(self, mock_connect):
        """Test connection closing."""
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            db_conn.connect()

            assert db_conn.is_connected()

            db_conn.close()

            assert not db_conn.is_connected()
            assert db_conn.connection is None

            # Verify close was called on the underlying connection
            mock_connection.close.assert_called_once()

    @patch("psycopg2.connect")
    def test_close_nonexistent_connection(self, mock_connect):
        """Test closing a non-existent connection."""
        db_conn = DatabaseConnection()
        # Should not raise an error
        db_conn.close()

    @patch("psycopg2.connect")
    def test_context_manager(self, mock_connect):
        """Test DatabaseConnection as a context manager."""
        mock_connection = MagicMock()
        mock_connection.get_dsn_parameters.return_value = {"dbname": "test_db"}
        mock_connection.closed = False
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            with DatabaseConnection() as db_conn:
                assert db_conn.is_connected()
                assert db_conn.get_database_name() == "test_db"

            # Should be disconnected after exiting context
            assert not db_conn.is_connected()

    @patch("psycopg2.connect")
    def test_destructor_closes_connection(self, mock_connect):
        """Test that destructor closes connection."""
        mock_connection = MagicMock()
        mock_connection.closed = False
        mock_connect.return_value = mock_connection

        with patch.dict(os.environ, self.test_env_vars, clear=True):
            db_conn = DatabaseConnection()
            db_conn.connect()

            # Active connection
            assert db_conn.is_connected()

            # Delete the object (simulate garbage collection)
            del db_conn

            # Checking if close was called on the underlying connection
            mock_connection.close.assert_called_once()


class TestDatabaseConnectionIntegration:
    """Integration tests for database connection with actual env vars as set by user"""

    def test_actual_environment_variable_parsing(self):
        db_conn = DatabaseConnection()

        assert not db_conn.is_connected()

        db_conn.connect()

        db_conn.get_connection()

        assert db_conn.is_connected()

        db_conn.close()
        assert not db_conn.is_connected()
