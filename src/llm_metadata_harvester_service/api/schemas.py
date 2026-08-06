from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator

from llm_metadata_harvester_service.core.config import BATCH_MAX_URLS

# Job submission
#=================

class JobSubmitRequest(BaseModel):
    model: str = Field(..., examples=["gemini-2.5-flash"])
    url: str = Field(
        ...,
        examples=["https://example.com or doi:10.5281/zenodo.12345"],
        description="URL, DOI, or other resolvable identifier",
    )
    webhook_url: HttpUrl | None = Field(
        None,
        description="Optional URL to be notified when the job completes or fails",
    )
    webhook_secret: str | None = Field(
        None,
        min_length=16,
        max_length=256,
        description="Optional secret used to HMAC-sign the webhook payload",
    )


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    webhook_url: str | None = None



# Job status
# ===============

class JobStatusResponse(BaseModel):
    job_id: str
    status: str



# Job result
# =================

class JobResultResponse(BaseModel):
    job_id: str
    status: str
    model: str
    result: dict[str, Any]
    logs: str



# Batch submission
# ================

class BatchSubmitRequest(BaseModel):
    model: str = Field(..., examples=["gemini-2.5-flash"])
    urls: list[str] = Field(
        ...,
        min_length=1,
        max_length=BATCH_MAX_URLS,
        description="List of URLs/DOIs to harvest; each URL becomes its own job",
    )
    webhook_url: HttpUrl | None = Field(
        None,
        description="Optional URL to be notified when each job completes or fails",
    )
    webhook_secret: str | None = Field(
        None,
        min_length=16,
        max_length=256,
        description="Optional secret used to HMAC-sign the webhook payload",
    )

    @field_validator("urls")
    @classmethod
    def _cap_batch_size(cls, urls: list[str]) -> list[str]:
        if len(urls) > BATCH_MAX_URLS:
            raise ValueError(f"Batch exceeds maximum of {BATCH_MAX_URLS} URLs")
        return urls


class BatchJobReference(BaseModel):
    job_id: str
    url: str
    status: str


class BatchSubmitResponse(BaseModel):
    batch_id: str
    status: str
    count: int
    jobs: list[BatchJobReference]



# Batch status
# ============

class BatchSummary(BaseModel):
    queued: int
    pending: int
    success: int
    failed: int
    total: int


class BatchStatusResponse(BaseModel):
    batch_id: str
    summary: BatchSummary
    jobs: list[BatchJobReference]



# Batch results
# =============

class BatchJobResult(BaseModel):
    job_id: str
    url: str
    status: str
    model: str | None = None
    result: dict[str, Any] | None = None
    logs: str | None = None
    error: str | None = None


class BatchResultResponse(BaseModel):
    batch_id: str
    jobs: list[BatchJobResult]
