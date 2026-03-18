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


async def get_jwt_secret_from_secrets(secret_id: str, region: str = "us-east-2") -> str:
    """Load JWT secret key from AWS Secrets Manager.

    Args:
        secret_id: AWS Secrets Manager secret ID
        region: AWS region

    Returns:
        JWT secret key string (minimum 32 characters for HS256)

    Raises:
        ValueError: If secret cannot be loaded
    """
    secrets = await load_secrets_from_aws(secret_id, region)
    jwt_secret = secrets.get("jwt_secret") or secrets.get("JWT_SECRET")

    if not jwt_secret or not isinstance(jwt_secret, str):
        raise ValueError(f"jwt_secret not found in secret {secret_id}")

    return str(jwt_secret)
