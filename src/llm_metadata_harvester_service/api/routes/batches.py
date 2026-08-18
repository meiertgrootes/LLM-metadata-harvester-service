from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from llm_metadata_harvester_service.api.schemas import (
    BatchJobReference,
    BatchJobResult,
    BatchResultResponse,
    BatchStatusResponse,
    BatchSummary,
)
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import get_db
from llm_metadata_harvester_service.db.status import JobStatus

router = APIRouter(prefix="/batches", tags=["batches"])


def _get_jobs(db: Session, batch_id: str) -> list[Job]:
    jobs = db.scalars(
        select(Job).where(Job.batch_id == batch_id).order_by(Job.created_at)
    ).all()
    if not jobs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Batch not found",
        )
    return list(jobs)


@router.get(
    "/{batch_id}",
    response_model=BatchStatusResponse,
)
def get_batch_status(
    batch_id: str,
    db: Session = Depends(get_db),
) -> BatchStatusResponse:
    """
    Aggregate status overview for a batch, plus per-job status.
    """
    jobs = _get_jobs(db, batch_id)
    counts = Counter(job.status for job in jobs)

    summary = BatchSummary(
        queued=counts.get(JobStatus.QUEUED, 0),
        pending=counts.get(JobStatus.PENDING, 0),
        success=counts.get(JobStatus.SUCCESS, 0),
        failed=counts.get(JobStatus.FAILURE, 0),
        total=len(jobs),
    )
    references = [
        BatchJobReference(job_id=job.job_id, url=job.url, status=job.status)
        for job in jobs
    ]

    return BatchStatusResponse(
        batch_id=batch_id,
        summary=summary,
        jobs=references,
    )


@router.get(
    "/{batch_id}/results",
    response_model=BatchResultResponse,
)
def get_batch_results(
    batch_id: str,
    db: Session = Depends(get_db),
) -> BatchResultResponse:
    """
    Full per-job results for a batch (individually identifiable).
    """
    jobs = _get_jobs(db, batch_id)
    results = [
        BatchJobResult(
            job_id=job.job_id,
            url=job.url,
            status=job.status,
            model=job.model,
            result=job.result,
            logs=job.logs,
            error=job.error,
        )
        for job in jobs
    ]

    return BatchResultResponse(
        batch_id=batch_id,
        jobs=results,
    )
