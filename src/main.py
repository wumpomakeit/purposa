"""Purposa — Multi-Agent DAO Governance Analysis & Voting Assistant.

Entry point for both the FastAPI web service and the CLI.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import router
from src.config import get_settings

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.write_onchainos_env()
    log.info(
        "purposa.startup",
        env=settings.purposa_env,
        okx_configured=settings.has_okx_credentials,
        llm_available=settings.has_llm_credentials,
        wallet_address_configured=bool(settings.seller_address),
    )
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Purposa",
        description=(
            "Multi-Agent DAO Governance Analysis & Voting Assistant.\n\n"
            "Built for the OKX AI Genesis Hackathon — Agent Service Provider (ASP) track.\n\n"
            "## How it works\n"
            "1. Call `POST /analyze` with a Snapshot proposal URL → receive HTTP 402\n"
            "2. Pay via OKX Agentic Wallet (`onchainos payment pay`)\n"
            "3. Replay request with `Authorization` header → receive multi-agent verdict\n"
            "4. Optionally call `POST /vote` to submit your vote via the Agentic Wallet\n\n"
            "## Payment\n"
            "Analysis is pay-per-call via x402 (OKX `exact` scheme). "
            "Vote submission is free (but requires Agentic Wallet login).\n\n"
            "## Audit\n"
            "Every analysis stores a full reasoning trace at `GET /trace/{trace_id}`."
        ),
        version="0.1.0",
        contact={"name": "Purposa", "url": "https://github.com/purposa/purposa"},
        license_info={"name": "MIT"},
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="")

    return app


app = create_app()


def cli() -> None:
    """CLI entry point — run the Purposa service."""
    import typer

    cli_app = typer.Typer(name="purposa", help="Purposa DAO Governance Assistant")

    @cli_app.command()
    def serve(
        host: str = typer.Option("0.0.0.0", help="Host to bind"),
        port: int = typer.Option(8000, help="Port to bind"),
        reload: bool = typer.Option(False, help="Enable auto-reload (dev only)"),
    ) -> None:
        """Start the Purposa HTTP service."""
        uvicorn.run("src.main:app", host=host, port=port, reload=reload)

    cli_app()


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host=settings.purposa_host,
        port=settings.purposa_port,
        reload=not settings.is_production,
        log_config=None,
    )
