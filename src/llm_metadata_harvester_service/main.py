from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text

from llm_metadata_harvester_service.api.routes.batches import router as batches_router
from llm_metadata_harvester_service.api.routes.jobs import router as jobs_router
from llm_metadata_harvester_service.core.config import CELERY_BROKER_URL
from llm_metadata_harvester_service.db.session import engine

app = FastAPI(title="LLM metadata harvester Service")

app.include_router(jobs_router)
app.include_router(batches_router)


def _check_postgres() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _check_redis() -> None:
    client = Redis.from_url(
        CELERY_BROKER_URL,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    try:
        client.ping()
    finally:
        client.close()


@app.get("/health/live")
def health_live() -> dict[str, str]:
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready() -> JSONResponse:
    checks = {"postgres": "ok", "redis": "ok"}
    try:
        _check_postgres()
    except Exception:
        checks["postgres"] = "error"
    try:
        _check_redis()
    except Exception:
        checks["redis"] = "error"

    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=(status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE),
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
