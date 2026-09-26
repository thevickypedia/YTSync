import asyncio
import pathlib
import sqlite3
from types import TracebackType
from typing import List, Tuple, Type


class AsyncDatabaseConnection:
    """Context manager for asynchronous SQLite database connections.

    >>> AsyncDatabaseConnection

    """

    connection: sqlite3.Connection

    def __init__(self, datastore: str, timeout: int):
        """Instantiates the database connection.

        Args:
            datastore: Filepath of the database file.
            timeout: Connection timeout in seconds.
        """
        self.datastore = datastore
        self.timeout = timeout

    async def __aenter__(self) -> sqlite3.Connection:
        """Creates and returns a database connection.

        Returns:
            sqlite3.Connection: An active connection to the database.
        """
        self.connection = await asyncio.to_thread(
            sqlite3.connect,
            self.datastore,
            check_same_thread=False,
            timeout=self.timeout,
        )
        return self.connection

    async def __aexit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Commits or rolls back the transaction and closes the connection.

        Args:
            exc_type: Exception type, if an exception was raised, otherwise ``None``.
            exc_val: Exception value, if an exception was raised, otherwise ``None``.
            exc_tb: Exception traceback, if an exception was raised, otherwise ``None``.
        """
        if exc_type:
            await asyncio.to_thread(self.connection.rollback)
        else:
            await asyncio.to_thread(self.connection.commit)

        await asyncio.to_thread(self.connection.close)


class Database:
    """Creates a connection to the base DB.

    >>> Database

    Args:
        database: Name of the database file.
        timeout: Timeout for the connection to a database.
    """

    def __init__(self, database: pathlib.Path | str, timeout: int = 3):
        """Instantiates the class ``Database`` with the given datastore and timeout options.

        Args:
            database: Database filepath.
            timeout: Connection timeout for the database.
        """
        self.datastore = str(database)
        self.timeout = timeout

    @property
    def connection(self) -> AsyncDatabaseConnection:
        """Creates a database connection.

        Returns:
            DatabaseConnection:
            Returns a ``DatabaseConnection`` object and ensures it is committed and closed after execution.
        """
        return AsyncDatabaseConnection(self.datastore, self.timeout)

    def create_table(self, table_name: str, columns: List[str] | Tuple[str], primary_key: str | None = None) -> None:
        """Creates the table with the required columns.

        Args:
            table_name: Name of the table that has to be created.
            columns: List of columns that has to be created.
            primary_key: Primary key.
        """
        if primary_key:
            if primary_key not in columns:
                raise ValueError(f"{primary_key!r} should be one of the columns")
            # Rebuild the column definition with PRIMARY KEY
            columns = [f"{col} PRIMARY KEY" if col == primary_key else col for col in columns]
        with sqlite3.connect(database=self.datastore, check_same_thread=False, timeout=self.timeout) as connection:
            cursor = connection.cursor()
            # Use f-string or %s as table names cannot be parametrized
            cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(columns)})")
