"""In-memory async job store for background backtest execution."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Coroutine

from .logging_config import get_logger

logger = get_logger(__name__)


class JobStatus(Enum):
    """Status of a background backtest job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class JobResult:
    """Result container for a background job."""

    job_id: str
    status: JobStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None
    estimated_seconds: float | None = None
    expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "estimated_seconds": self.estimated_seconds,
            "expires_at": self.expires_at,
        }


class JobStore:
    """In-memory store for async backtest jobs."""

    def __init__(self) -> None:
        """Initialize empty job store."""
        self._jobs: dict[str, JobResult] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def submit_job(
        self,
        coro: Coroutine[Any, Any, dict[str, Any]],
        estimated_seconds: float | None = None,
        expires_at: str | None = None,
    ) -> str:
        """Submit a coroutine for background execution.

        Args:
            coro: Async coroutine that returns a result dict.
            estimated_seconds: Estimated compute time for the job.
            expires_at: ISO timestamp when job result expires.

        Returns:
            Job ID string.

        """
        job_id = f"bt_{uuid.uuid4().hex[:8]}"
        self._jobs[job_id] = JobResult(
            job_id=job_id,
            status=JobStatus.QUEUED,
            estimated_seconds=estimated_seconds,
            expires_at=expires_at,
        )
        task = asyncio.create_task(self._run_job(job_id, coro))
        self._tasks[job_id] = task
        logger.info("job_submitted", job_id=job_id, estimated_seconds=estimated_seconds)
        return job_id

    def get_job(
        self,
        job_id: str,
        ttl_seconds: int | None = None,
    ) -> JobResult | None:
        """Get job result by ID, evicting expired completed jobs.

        Args:
            job_id: Job identifier.
            ttl_seconds: If set, evict completed jobs older than this.

        Returns:
            JobResult or None if not found or expired.

        """
        job = self._jobs.get(job_id)
        if job is None:
            return None
        if ttl_seconds is not None and job.completed_at:
            completed = datetime.fromisoformat(job.completed_at)
            age = (datetime.now(UTC) - completed).total_seconds()
            if age > ttl_seconds:
                del self._jobs[job_id]
                return None
        return job

    async def _run_job(
        self,
        job_id: str,
        coro: Coroutine[Any, Any, dict[str, Any]],
    ) -> None:
        """Execute a job coroutine and update status.

        Args:
            job_id: Job identifier.
            coro: Coroutine to execute.

        """
        job = self._jobs[job_id]
        job.status = JobStatus.RUNNING
        logger.info("job_started", job_id=job_id)

        try:
            result = await coro
            job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.now(UTC).isoformat()
            logger.info("job_completed", job_id=job_id)
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error = str(exc)
            job.completed_at = datetime.now(UTC).isoformat()
            logger.exception("job_failed", job_id=job_id, error=str(exc))
        finally:
            self._tasks.pop(job_id, None)
