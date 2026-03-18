"""Session management for OBaI agents.

Provides DynamoDB-backed session for distributed conversation memory.

Usage:
    ```python
    from core_agents.session import DynamoDBSession

    # Create session for a user/conversation
    session = DynamoDBSession("user_123")

    # Use with Agent SDK Runner
    result = await Runner.run(agent, "Hello", session=session)

    # Session automatically persists conversation history
    # across multiple turns and bot instances
    ```

Environment Variables:
    SESSION_TABLE_NAME: DynamoDB table name (default: obai_sessions_dev)
    SESSION_REGION: AWS region (default: us-east-2)
    SESSION_ENDPOINT_URL: Custom endpoint for LocalStack
    SESSION_TTL_DAYS: Session retention in days (default: 30)
"""

from core_agents.session.dynamo_session import DynamoDBSession

__all__ = ["DynamoDBSession"]
