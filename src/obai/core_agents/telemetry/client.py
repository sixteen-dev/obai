"""Async DynamoDB client for telemetry storage.

Uses aioboto3 for non-blocking DynamoDB operations.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aioboto3  # type: ignore[import-untyped]  # no py.typed marker
    from types_aiobotocore_dynamodb.service_resource import Table

    from core_agents.telemetry.config import TelemetryConfig
    from core_agents.telemetry.models import TraceItem

logger = logging.getLogger(__name__)


class DynamoClientError(Exception):
    """Base exception for DynamoDB client errors."""


class DynamoConnectionError(DynamoClientError):
    """Failed to connect to DynamoDB."""


class DynamoWriteError(DynamoClientError):
    """Failed to write to DynamoDB."""


class DynamoClient:
    """Async DynamoDB client for trace storage.

    Uses aioboto3 for non-blocking operations. Designed for fire-and-forget
    writes where performance is critical.

    Example:
        ```python
        config = get_telemetry_config()
        async with DynamoClient(config) as client:
            await client.put_trace(trace_item)
        ```
    """

    def __init__(self, config: TelemetryConfig) -> None:
        """Initialize the client.

        Args:
            config: Telemetry configuration.
        """
        self._config = config
        self._session: aioboto3.Session | None = None
        self._resource: Any = None  # DynamoDB resource
        self._table: Table | None = None
        self._context_manager: Any = None

    async def connect(self) -> None:
        """Connect to DynamoDB.

        Raises:
            DynamoConnectionError: If connection fails.
        """
        if self._table is not None:
            return

        # Lazy-import aioboto3/botocore — optional deps ([project.optional-dependencies] telemetry).
        import aioboto3
        from botocore.exceptions import ClientError

        try:
            self._session = aioboto3.Session()

            resource_kwargs: dict[str, str] = {
                "region_name": self._config.region,
            }
            if self._config.endpoint_url:
                resource_kwargs["endpoint_url"] = self._config.endpoint_url

            self._context_manager = self._session.resource("dynamodb", **resource_kwargs)
            self._resource = await self._context_manager.__aenter__()
            self._table = await self._resource.Table(self._config.table_name)

            logger.debug(f"Connected to DynamoDB table: {self._config.table_name}")

        except ClientError as e:
            msg = f"Failed to connect to DynamoDB: {e}"
            logger.error(msg)
            raise DynamoConnectionError(msg) from e

    async def close(self) -> None:
        """Close the DynamoDB connection."""
        if self._context_manager is not None:
            try:
                await self._context_manager.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing DynamoDB connection: {e}")
            finally:
                self._table = None
                self._resource = None
                self._context_manager = None
                self._session = None

    async def put_trace(self, item: TraceItem) -> None:
        """Write a single trace item to DynamoDB.

        Args:
            item: The trace item to write.

        Raises:
            DynamoWriteError: If write fails.
        """
        if self._table is None:
            await self.connect()

        if self._table is None:
            msg = "DynamoDB table not initialized"
            raise DynamoWriteError(msg)

        from botocore.exceptions import ClientError

        try:
            await self._table.put_item(Item=item.to_dynamo_item())
            logger.debug(f"Wrote trace {item.trace_id} to DynamoDB")

        except ClientError as e:
            msg = f"Failed to write trace {item.trace_id}: {e}"
            logger.error(msg)
            raise DynamoWriteError(msg) from e

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Get a trace by ID using GSI.

        Args:
            trace_id: The trace ID to look up.

        Returns:
            Trace data or None if not found.
        """
        if self._table is None:
            await self.connect()

        if self._table is None:
            return None

        from botocore.exceptions import ClientError

        try:
            response = await self._table.query(
                IndexName="trace-id-index",
                KeyConditionExpression="trace_id = :tid",
                ExpressionAttributeValues={":tid": trace_id},
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None

        except ClientError as e:
            logger.error(f"Failed to get trace {trace_id}: {e}")
            return None

    async def query_session(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Get all traces for a session.

        Args:
            session_id: The session ID.
            limit: Maximum number of traces to return.

        Returns:
            List of trace data, ordered by timestamp.
        """
        if self._table is None:
            await self.connect()

        if self._table is None:
            return []

        from botocore.exceptions import ClientError

        try:
            response = await self._table.query(
                KeyConditionExpression="pk = :pk",
                ExpressionAttributeValues={":pk": f"SESSION#{session_id}"},
                ScanIndexForward=True,  # Oldest first
                Limit=limit,
            )
            return response.get("Items", [])

        except ClientError as e:
            logger.error(f"Failed to query session {session_id}: {e}")
            return []

    async def query_by_status(
        self,
        status: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Query traces by status (success, error, guardrail_rejected).

        Args:
            status: Status to filter by.
            limit: Maximum number of traces to return.

        Returns:
            List of trace data, newest first.
        """
        if self._table is None:
            await self.connect()

        if self._table is None:
            return []

        from botocore.exceptions import ClientError

        try:
            response = await self._table.query(
                IndexName="status-timestamp-index",
                KeyConditionExpression="status = :s",
                ExpressionAttributeValues={":s": status},
                ScanIndexForward=False,  # Newest first
                Limit=limit,
            )
            return response.get("Items", [])

        except ClientError as e:
            logger.error(f"Failed to query by status {status}: {e}")
            return []

    async def __aenter__(self) -> DynamoClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()
