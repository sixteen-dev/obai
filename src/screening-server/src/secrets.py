"""Load secrets from AWS Secrets Manager at runtime."""

import json
from typing import Any

import aioboto3


async def load_secrets_from_aws(secret_id: str, region: str = "us-east-2") -> dict[str, Any]:
    """Load all secrets from AWS Secrets Manager as a dictionary.

    Args:
        secret_id: AWS Secrets Manager secret ID
        region: AWS region

    Returns:
        Dictionary of all secrets from the specified secret

    Raises:
        ValueError: If secret cannot be loaded or parsed
    """
    session = aioboto3.Session()
    async with session.client("secretsmanager", region_name=region) as client:
        try:
            response = await client.get_secret_value(SecretId=secret_id)

            if "SecretString" not in response:
                raise ValueError("Secret is binary, expected JSON string")

            secret_string = response["SecretString"]

            try:
                parsed: dict[str, Any] = json.loads(secret_string)
                return parsed
            except json.JSONDecodeError as e:
                raise ValueError(f"Secret is not valid JSON: {e}") from e

        except Exception as e:
            raise ValueError(f"Failed to load secret from {secret_id}: {e}") from e
