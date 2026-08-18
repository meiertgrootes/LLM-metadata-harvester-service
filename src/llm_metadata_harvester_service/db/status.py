from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    PENDING = "pending"
    SUCCESS = "success"
    FAILURE = "failure"


TERMINAL_JOB_STATUSES = (JobStatus.SUCCESS, JobStatus.FAILURE)
