from fastapi import FastAPI

from llm_metadata_harvester_service.api.routes.jobs import router as jobs_router

app = FastAPI(title="LLM metadata harvester Service")

app.include_router(jobs_router)
