"""AWS Secrets Manager integration for portfolio-server."""

import json
from typing import Any

import aioboto3


async def load_secrets_from_aws(
    secret_id: str,
    region: str = "us-east-2",
) -> dict[str, Any]:
    """Load all secrets from AWS Secrets Manager as a dictionary.

    Args:
        secret_id: The AWS Secrets Manager secret ID.
        region: AWS region name.

    Returns:
        Dictionary containing all secret key-value pairs.

    Raises:
        ValueError: If secret cannot be loaded or is not valid JSON.

    """
    session = aioboto3.Session()
    async with session.client("secretsmanager", region_name=region) as client:
        try:
            response = await client.get_secret_value(SecretId=secret_id)

            if "SecretString" not in response:
                msg = "Secret is binary, expected JSON string"
                raise ValueError(msg)

            secret_string = response["SecretString"]

            try:
                parsed: dict[str, Any] = json.loads(secret_string)
                return parsed
            except json.JSONDecodeError as e:
                msg = f"Secret is not valid JSON: {e}"
                raise ValueError(msg) from e

        except Exception as e:
            msg = f"Failed to load secret from {secret_id}: {e}"
            raise ValueError(msg) from e
