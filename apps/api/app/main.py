from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.errors import install_error_handlers
from app.core.telemetry import init_telemetry
from app.routers import auth, automations, core, executions, terminal

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="AGENT//98 API",
        version="0.1.0",
        description="The AI researches. The system validates. The user signs. The chain executes.",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["content-type", "authorization", "idempotency-key"],
    )

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):
        # CSRF defense in depth: cookie-authenticated mutations must come from an allowed origin.
        origin = request.headers.get("origin")
        if request.method in MUTATING and origin and origin not in s.cors_origins:
            return JSONResponse(status_code=403, content={"error": {"code": "bad_origin", "message": "Origin not allowed", "details": None}})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
        return response

    install_error_handlers(app)
    init_telemetry(app)
    for r in (core.router, auth.router, executions.router, terminal.router, automations.router):
        app.include_router(r, prefix="/v1")
    return app


app = create_app()
