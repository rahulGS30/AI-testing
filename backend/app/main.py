from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, personas, test_cases

app = FastAPI(
    title="AI Persona UX Testing Agent",
    description="A phased foundation for evidence-based, persona-driven UX testing.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

app.include_router(health.router)
app.include_router(personas.router)
app.include_router(test_cases.router)
app.include_router(test_cases.runs_router)
