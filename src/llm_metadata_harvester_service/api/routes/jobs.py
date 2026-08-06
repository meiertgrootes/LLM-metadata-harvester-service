import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from llm_metadata_harvester_service.api.schemas import (
    BatchJobReference,
    BatchSubmitRequest,
    BatchSubmitResponse,
    JobResultResponse,
    JobStatusResponse,
    JobSubmitRequest,
    JobSubmitResponse,
)
from llm_metadata_harvester_service.db.models import Job
from llm_metadata_harvester_service.db.session import get_db
from llm_metadata_harvester_service.workers.tasks import run_harvester_task

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _enqueue(
    *,
    job_id: str,
    url: str,
    model: str,
    api_key: str,
    webhook_url: str | None,
    webhook_secret: str | None,
) -> None:
    run_harvester_task.apply_async(
        kwargs={
            "model": model,
            "url": url,
            "api_key": api_key,
            "webhook_url": webhook_url,
            "webhook_secret": webhook_secret,
        },
        task_id=job_id,
    )


@router.post(
    "/batch/",
    response_model=BatchSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_batch(
    payload: BatchSubmitRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> BatchSubmitResponse:
    """
    Submit a batch of metadata harvesting jobs (one per URL).
    """
    batch_id = str(uuid.uuid4())
    webhook_url = str(payload.webhook_url) if payload.webhook_url else None

    jobs = []
    for url in payload.urls:
        job = Job(
            job_id=str(uuid.uuid4()),
            batch_id=batch_id,
            model=payload.model,
            url=url,
            status="queued",
            webhook_url=webhook_url,
        )
        db.add(job)
        jobs.append(job)
    db.commit()

    references = []
    for job in jobs:
        try:
            _enqueue(
                job_id=job.job_id,
                url=job.url,
                model=payload.model,
                api_key=x_api_key,
                webhook_url=webhook_url,
                webhook_secret=payload.webhook_secret,
            )
        except Exception:
            job.status = "failure"
            job.error = "dispatch_failed"
            job.completed_at = datetime.now(UTC)
        references.append(
            BatchJobReference(job_id=job.job_id, url=job.url, status=job.status)
        )
    db.commit()

    return BatchSubmitResponse(
        batch_id=batch_id,
        status="queued",
        count=len(references),
        jobs=references,
    )


@router.post(
    "/",
    response_model=JobSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_job(
    payload: JobSubmitRequest,
    x_api_key: str = Header(..., alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> JobSubmitResponse:
    """
    Submit a metadata harvesting job.
    """
    job_id = str(uuid.uuid4())
    webhook_url = str(payload.webhook_url) if payload.webhook_url else None

    job = Job(
        job_id=job_id,
        model=payload.model,
        url=payload.url,
        status="queued",
        webhook_url=webhook_url,
    )
    db.add(job)
    db.commit()

    try:
        _enqueue(
            job_id=job_id,
            url=payload.url,
            model=payload.model,
            api_key=x_api_key,
            webhook_url=webhook_url,
            webhook_secret=payload.webhook_secret,
        )
    except Exception:
        job.status = "failure"
        job.error = "dispatch_failed"
        job.completed_at = datetime.now(UTC)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Failed to enqueue job",
        )

    return JobSubmitResponse(
        job_id=job_id,
        status="queued",
        webhook_url=webhook_url,
    )


@router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
)
def get_job_status(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobStatusResponse:
    """
    Lightweight status endpoint (no payload).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status,
    )


@router.get(
    "/{job_id}/result",
    response_model=JobResultResponse,
)
def get_job_result(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobResultResponse:
    """
    Retrieve job result, logs, and model used.
    """
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )

    if job.status in ("queued", "pending"):
        raise HTTPException(
            status_code=status.HTTP_202_ACCEPTED,
            detail="Job still pending",
        )

    if job.status == "failure":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=job.error or "Harvest failed",
        )

    if job.status != "success":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job in state: {job.status}",
        )

    return JobResultResponse(
        job_id=job.job_id,
        status="success",
        model=job.model,
        result=job.result or {},
        logs=job.logs or "",
    )
