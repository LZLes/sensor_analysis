from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .routers import auth, calibration, datasets, export, insights, projects

settings = get_settings()

app = FastAPI(title="Sensor Analysis Studio API")

app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(datasets.router)
app.include_router(calibration.router)
app.include_router(insights.router)
app.include_router(export.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
