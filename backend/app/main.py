from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import candidates, career_health, copilot, jobs, resume
from app.config import get_settings

app = FastAPI(
    title="ApplyForMe AI Engine",
    description="API Gateway for the ApplyForMe Career Command Center's AI Orchestrator.",
    root_path=get_settings().root_path,
)


@app.exception_handler(ValueError)
def handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
    # Every engine's gather_context() raises ValueError for "the referenced
    # candidate/job/resume doesn't exist" -- without this they'd all surface
    # as raw 500s with a full traceback instead of a clean 404.
    return JSONResponse(status_code=404, content={"detail": str(exc)})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(candidates.router)
app.include_router(resume.router)
app.include_router(jobs.router)
app.include_router(career_health.router)
app.include_router(copilot.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
