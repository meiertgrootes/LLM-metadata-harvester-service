from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from llm_metadata_harvester_service.api.routes.batches import router as batches_router
from llm_metadata_harvester_service.api.routes.jobs import router as jobs_router
from llm_metadata_harvester_service.db.init import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title="LLM metadata harvester Service", lifespan=lifespan)

app.include_router(jobs_router)
app.include_router(batches_router)
