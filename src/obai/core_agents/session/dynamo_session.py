"""DynamoDB-based session for distributed conversation memory.

Implements the OpenAI Agent SDK Session protocol using DynamoDB,
enabling persistent, distributed conversation history across
multiple instances (e.g., Discord bot scaling).

Usage:
    ```python
    from core_agents.session import DynamoDBSession

    session = DynamoDBSession("user_123")
    result = await Runner.run(agent, "Hello", session=session)
    ```

Environment Variables:
    SESSION_TABLE_NAME: DynamoDB table name (default: obai_sessions_dev)
    SESSION_REGION: AWS region (default: us-east-2)
    SESSION_ENDPOINT_URL: Custom endpoint for LocalStack
    SESSION_TTL_DAYS: Session retention in days (default: 30)
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from agents.items import TResponseInputItem
from agents.memory.session import SessionABC

if TYPE_CHECKING:
    from types_aiobotocore_dynamodb.service_resource import Table

logger = logging.getLogger(__name__)


class DynamoDBSession(SessionABC):
    """DynamoDB-backed session for conversation memory.

    Stores conversation items as JSON in DynamoDB, enabling:
    - Distributed access (multiple bot instances)
    - Persistent memory across restarts
    - Automatic TTL-based cleanup

    Schema:
        PK: SESSION#{session_id}
        SK: MSG#{timestamp}#{sequence}
        message_data: JSON-serialized TResponseInputItem
        created_at: ISO timestamp
        ttl: Unix timestamp for auto-deletion
    """

    def __init__(
        self,
        session_id: str,
        table_name: str | None = None,
        region: str | None = None,
        endpoint_url: str | None = None,
        ttl_days: int = 30,
    ) -> None:
        """Initialize DynamoDB session.

        Args:
            session_id: Unique identifier for this conversation.
            table_name: DynamoDB table name. Defaults to env var or 'obai_sessions_dev'.
            region: AWS region. Defaults to env var or 'us-east-2'.
            endpoint_url: Custom endpoint for LocalStack/local testing.
            ttl_days: Days until session data auto-deletes. Default 30.
        """
        import os

        self.session_id = session_id
        self.table_name = table_name or os.getenv("SESSION_TABLE_NAME", "obai_sessions_dev")
        self.region = region or os.getenv("SESSION_REGION", "us-east-2")
        self.endpoint_url = endpoint_url or os.getenv("SESSION_ENDPOINT_URL")
        self.ttl_days = ttl_days

        # Lazy-import aioboto3 — optional dep ([project.optional-dependencies] telemetry).
        import aioboto3  # type: ignore[import-untyped]

        self._aioboto_session = aioboto3.Session()
        self._sequence = 0  # For ordering items within same millisecond

    def _get_pk(self) -> str:
        """Get partition key for this session."""
        return f"SESSION#{self.session_id}"

    def _get_sk(self, timestamp: datetime | None = None, sequence: int | None = None) -> str:
        """Get sort key for a message item."""
        ts = timestamp or datetime.now(tz=UTC)
        seq = sequence if sequence is not None else self._sequence
        return f"MSG#{ts.isoformat()}#{seq:06d}"

    def _get_ttl(self) -> int:
        """Calculate TTL timestamp."""
        return int(time.time()) + (self.ttl_days * 24 * 60 * 60)

    async def _get_table(self) -> Table:
        """Get DynamoDB table resource."""
        async with self._aioboto_session.resource(
            "dynamodb",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
        ) as dynamodb:
            return await dynamodb.Table(self.table_name)

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        """Retrieve conversation history from DynamoDB.

        Args:
            limit: Maximum number of items to retrieve. If None, retrieves all.
                   Returns the latest N items in chronological order.

        Returns:
            List of conversation items.
        """
        from botocore.exceptions import ClientError

        try:
            async with self._aioboto_session.resource(
                "dynamodb",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
            ) as dynamodb:
                table = await dynamodb.Table(self.table_name)

                # Query all items for this session
                query_params: dict[str, Any] = {
                    "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk_prefix)",
                    "ExpressionAttributeValues": {
                        ":pk": self._get_pk(),
                        ":sk_prefix": "MSG#",
                    },
                    "ScanIndexForward": True,  # Chronological order
                }

                response = await table.query(**query_params)
                items = response.get("Items", [])

                # Handle pagination
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = await table.query(**query_params)
                    items.extend(response.get("Items", []))

                # Deserialize items
                result: list[TResponseInputItem] = []
                for item in items:
                    try:
                        message_data = item.get("message_data", "{}")
                        parsed = json.loads(message_data)
                        result.append(parsed)
                    except json.JSONDecodeError:
                        logger.warning(f"Skipping invalid JSON in session {self.session_id}")
                        continue

                # Apply limit (return last N items)
                if limit is not None and len(result) > limit:
                    result = result[-limit:]

                logger.debug(f"Retrieved {len(result)} items for session {self.session_id}")
                return result

        except ClientError as e:
            logger.error(f"DynamoDB error retrieving items: {e}")
            return []

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        """Store new items in DynamoDB.

        Args:
            items: List of conversation items to store.
        """
        if not items:
            return

        from botocore.exceptions import ClientError

        try:
            async with self._aioboto_session.resource(
                "dynamodb",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
            ) as dynamodb:
                table = await dynamodb.Table(self.table_name)

                now = datetime.now(tz=UTC)
                ttl = self._get_ttl()

                # Write items with sequential sort keys
                async with table.batch_writer() as batch:
                    for item in items:
                        self._sequence += 1
                        sk = self._get_sk(now, self._sequence)

                        db_item = {
                            "pk": self._get_pk(),
                            "sk": sk,
                            "message_data": json.dumps(item),
                            "created_at": now.isoformat(),
                            "ttl": ttl,
                        }
                        await batch.put_item(Item=db_item)

                logger.debug(f"Added {len(items)} items to session {self.session_id}")

        except ClientError as e:
            logger.error(f"DynamoDB error adding items: {e}")
            raise

    async def pop_item(self) -> TResponseInputItem | None:
        """Remove and return the most recent item.

        Returns:
            The most recent item, or None if session is empty.
        """
        from botocore.exceptions import ClientError

        try:
            async with self._aioboto_session.resource(
                "dynamodb",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
            ) as dynamodb:
                table = await dynamodb.Table(self.table_name)

                # Query for the last item (descending order, limit 1)
                response = await table.query(
                    KeyConditionExpression="pk = :pk AND begins_with(sk, :sk_prefix)",
                    ExpressionAttributeValues={
                        ":pk": self._get_pk(),
                        ":sk_prefix": "MSG#",
                    },
                    ScanIndexForward=False,  # Reverse chronological
                    Limit=1,
                )

                items = response.get("Items", [])
                if not items:
                    return None

                last_item = items[0]
                sk = last_item["sk"]

                # Delete the item
                await table.delete_item(
                    Key={"pk": self._get_pk(), "sk": sk},
                )

                # Deserialize and return
                message_data = last_item.get("message_data", "{}")
                return json.loads(message_data)

        except ClientError as e:
            logger.error(f"DynamoDB error popping item: {e}")
            return None
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON when popping from session {self.session_id}")
            return None

    async def clear_session(self) -> None:
        """Clear all items for this session."""
        from botocore.exceptions import ClientError

        try:
            async with self._aioboto_session.resource(
                "dynamodb",
                region_name=self.region,
                endpoint_url=self.endpoint_url,
            ) as dynamodb:
                table = await dynamodb.Table(self.table_name)

                # Query all items
                response = await table.query(
                    KeyConditionExpression="pk = :pk",
                    ExpressionAttributeValues={":pk": self._get_pk()},
                    ProjectionExpression="pk, sk",  # Only need keys for deletion
                )

                items = response.get("Items", [])

                # Handle pagination
                while "LastEvaluatedKey" in response:
                    response = await table.query(
                        KeyConditionExpression="pk = :pk",
                        ExpressionAttributeValues={":pk": self._get_pk()},
                        ProjectionExpression="pk, sk",
                        ExclusiveStartKey=response["LastEvaluatedKey"],
                    )
                    items.extend(response.get("Items", []))

                # Batch delete
                if items:
                    async with table.batch_writer() as batch:
                        for item in items:
                            await batch.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

                logger.info(f"Cleared {len(items)} items from session {self.session_id}")

        except ClientError as e:
            logger.error(f"DynamoDB error clearing session: {e}")
            raise
