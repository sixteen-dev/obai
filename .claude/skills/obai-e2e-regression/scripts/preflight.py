#!/usr/bin/env python3
"""Pre-flight readiness check for the OBaI e2e regression suite.

Verifies:
1. OPENAI_API_KEY is set.
2. Opik is reachable at the configured URL.
3. `obai status` reports every configured MCP server healthy.

Exits 0 if ready, non-zero with a clear reason otherwise.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TIMEOUT_S = 5.0
OBAI_STATUS_TIMEOUT_S = 30.0
CREDENTIAL_DIGEST_DOMAIN = b"obai-e2e-regression/openai-credential/v1\0"

_URL_USERINFO_RE = re.compile(r"(?i)\b(https?://)([^/\s?#]+@)")
_BEARER_RE = re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)\b(OPENAI_API_KEY|API[_-]?KEY|ACCESS[_-]?TOKEN|AUTH(?:ORIZATION)?|"
    r"PASSWORD|PASSWD|SECRET|TOKEN)(\s*[:=]\s*)([^\s&;,]+)"
)


class CredentialConfigurationError(ValueError):
    """The effective CLI credential is missing or unusable."""


def effective_cli_environment(
    *, env_file: Path | None = None, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Resolve environment values with the OBaI CLI's exact file precedence."""
    effective = dict(os.environ if base_env is None else base_env)
    path = env_file or (Path.home() / ".obai" / ".env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return effective
    except OSError as exc:
        raise CredentialConfigurationError(
            f"cannot read CLI-managed environment file: {type(exc).__name__}"
        ) from exc
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in effective:
            effective[key] = value.strip().strip("'\"")
    return effective


def redact_sensitive_text(value: object) -> str:
    """Remove common URL and credential forms before logging or persistence."""
    text = str(value)
    text = _URL_USERINFO_RE.sub(r"\1[REDACTED]@", text)
    text = _BEARER_RE.sub(r"\1 [REDACTED]", text)
    text = _OPENAI_KEY_RE.sub("[REDACTED]", text)
    return _SENSITIVE_VALUE_RE.sub(r"\1\2[REDACTED]", text)


def normalize_opik_url(value: str) -> str:
    """Return the Opik UI base URL, never the SDK's trailing /api URL."""
    normalized = value.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(normalized)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Opik URL must not contain userinfo credentials")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Opik URL must be an absolute HTTP(S) URL")
    if parsed.query or parsed.fragment:
        raise ValueError("Opik base URL must not contain a query string or fragment")
    if parsed.path not in {"", "/", "/api"}:
        raise ValueError("Opik URL path must be empty or exactly /api")
    if normalized.endswith("/api"):
        normalized = normalized[: -len("/api")]
    return normalized.rstrip("/")


def _configured_nonempty_value(
    effective: dict[str, str],
    *,
    primary: str,
    fallback: str,
    default: str,
) -> str:
    """Resolve OBaI's setting precedence without hiding explicit empty values."""
    if primary in effective:
        raw = effective[primary]
        source = primary
    elif fallback in effective:
        raw = effective[fallback]
        source = fallback
    else:
        return default
    if not raw.strip():
        raise ValueError(f"{source} must be non-empty when configured")
    return raw


def configured_opik_url(*, effective: dict[str, str] | None = None) -> str:
    effective = effective_cli_environment() if effective is None else effective
    raw = _configured_nonempty_value(
        effective,
        primary="OPIK_URL",
        fallback="OPIK_URL_OVERRIDE",
        default="http://localhost:5173",
    )
    return normalize_opik_url(raw)


def configured_opik_project(*, effective: dict[str, str] | None = None) -> str:
    effective = effective_cli_environment() if effective is None else effective
    return _configured_nonempty_value(
        effective,
        primary="OPIK_OBAI_PROJECT_NAME",
        fallback="OPIK_PROJECT_NAME",
        default="obai-eval",
    )


def effective_regression_environment(
    *, env_file: Path | None = None, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Return the exact normalized environment passed to OBaI by this gate.

    OBaI's ``AgentConfig`` consumes ``OPIK_URL`` and
    ``OPIK_OBAI_PROJECT_NAME``. The Opik SDK separately consumes
    ``OPIK_URL_OVERRIDE``. Normalize legacy aliases and ``/api`` suffixes once
    so preflight, runtime health checks, trace emission, and trace lookup cannot
    silently target different endpoints.
    """
    effective = effective_cli_environment(env_file=env_file, base_env=base_env)
    opik_url = configured_opik_url(effective=effective)
    opik_project = configured_opik_project(effective=effective)
    effective["OPIK_URL"] = opik_url
    effective["OPIK_URL_OVERRIDE"] = f"{opik_url}/api"
    effective["OPIK_OBAI_PROJECT_NAME"] = opik_project
    effective["OPIK_PROJECT_NAME"] = opik_project
    return effective


def _fail(msg: str) -> int:
    sys.stderr.write(f"PREFLIGHT FAIL: {redact_sensitive_text(msg)}\n")
    return 1


def effective_openai_credential_identity(*, env_file: Path | None = None) -> dict[str, str | int]:
    """Return a stable, non-reversible identity for the credential OBaI will use.

    Environment precedence deliberately means edits to an inactive
    ``~/.obai/.env`` do not alter this identity.  Only a domain-separated
    digest and a non-sensitive origin label leave this function.
    """
    effective = effective_cli_environment(env_file=env_file)
    if "OPENAI_API_KEY" in os.environ:
        secret = effective["OPENAI_API_KEY"]
        if not secret.strip():
            raise CredentialConfigurationError(
                "OPENAI_API_KEY is present in the environment but empty."
            )
        origin = "environment"
    else:
        secret = effective.get("OPENAI_API_KEY", "")
        if not secret.strip():
            raise CredentialConfigurationError(
                "OPENAI_API_KEY is not set in the environment or ~/.obai/.env."
            )
        origin = "cli_env_file"
    digest = hashlib.sha256(CREDENTIAL_DIGEST_DOMAIN + secret.encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "origin": origin,
        "digest_sha256": digest,
    }


def check_openai_key(*, env_file: Path | None = None) -> str | None:
    """Discover the key exactly where the OBaI CLI does, without logging it."""
    try:
        effective_openai_credential_identity(env_file=env_file)
    except CredentialConfigurationError as exc:
        return redact_sensitive_text(exc)
    return None


def check_opik() -> str | None:
    try:
        opik_url = configured_opik_url()
        opik_project = configured_opik_project()
    except ValueError as exc:
        return redact_sensitive_text(exc)
    query = urllib.parse.urlencode({"project_name": opik_project, "size": 1})
    url = f"{opik_url}/api/v1/private/traces?{query}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            if resp.status != 200:
                return redact_sensitive_text(f"Opik returned HTTP {resp.status} for {url}")
    except urllib.error.URLError as e:
        return redact_sensitive_text(f"Opik not reachable at {opik_url}: {e}")
    except OSError as e:
        return redact_sensitive_text(f"Opik connection error: {e}")
    return None


def check_obai_status() -> str | None:
    try:
        effective_env = effective_regression_environment()
        result = subprocess.run(
            ["uv", "run", "obai", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=OBAI_STATUS_TIMEOUT_S,
            check=False,
            env=effective_env,
        )
    except ValueError as exc:
        return redact_sensitive_text(exc)
    except subprocess.TimeoutExpired:
        return "obai status timed out — MCP servers may be hung."
    except FileNotFoundError:
        return "`uv` not on PATH — install uv or activate the venv."

    if not result.stdout.strip():
        return redact_sensitive_text(
            f"obai status returned no output (exit={result.returncode}): "
            f"{result.stderr.strip()[:300]}"
        )

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return redact_sensitive_text(
            f"obai status returned non-JSON: {e} — stdout head: {result.stdout[:200]}"
        )

    if not payload.get("all_healthy"):
        servers = payload.get("servers")
        if not isinstance(servers, list):
            servers = []
        down = [
            str(server.get("name", "unknown"))
            for server in servers
            if isinstance(server, dict) and server.get("status") != "ok"
        ]
        return f"MCP servers down: {', '.join(down) or 'unknown'}"
    return None


def main() -> int:
    checks = [
        ("OPENAI_API_KEY", check_openai_key),
        ("Opik reachable", check_opik),
        ("obai status (all configured MCP servers)", check_obai_status),
    ]
    failed = False
    for label, fn in checks:
        problem = fn()
        if problem:
            sys.stderr.write(f"  [FAIL] {label}: {problem}\n")
            failed = True
        else:
            sys.stdout.write(f"  [OK]   {label}\n")
    if failed:
        return _fail("one or more checks failed; fix and re-run preflight.")
    sys.stdout.write("\nReady.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
