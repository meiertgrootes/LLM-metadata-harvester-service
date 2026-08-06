from typing import Any

from pydantic import BaseModel, Field, HttpUrl

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
