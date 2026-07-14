"""Purposa — Multi-Agent DAO Governance Analysis & Voting Assistant.

Entry point for both the FastAPI web service and the CLI.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import router
from src.config import get_settings

log = structlog.get_logger(__name__)


def _auto_login_wallet(settings) -> bool:
    """
    Run `onchainos wallet login` (AK mode) at startup.

    In Railway (and any other ephemeral container), the onchainos session files
    (session.json, wallets.json, keyring.enc) don't exist until wallet login is
    called at least once. With OKX_API_KEY + SECRET + PASSPHRASE in env, this
    is fully non-interactive — no email, no OTP. Takes ~1 second.

    Safe to call on every restart: idempotent, just refreshes the session.
    Returns True on success, False on any failure (non-fatal).
    """
    import subprocess

    if not settings.has_okx_credentials:
        log.warning("wallet.auto_login_skipped", reason="OKX credentials not configured")
        return False

    import os
    env = os.environ.copy()
    env["OKX_API_KEY"] = settings.okx_api_key
    env["OKX_SECRET_KEY"] = settings.okx_secret_key
    env["OKX_PASSPHRASE"] = settings.okx_passphrase

    try:
        result = subprocess.run(
            [settings.onchainos_bin, "wallet", "login"],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        if result.returncode == 0:
            import json
            data = json.loads(result.stdout).get("data", {})
            log.info(
                "wallet.auto_login_ok",
                account=data.get("accountName"),
                account_id=data.get("accountId"),
            )
            return True
        log.warning("wallet.auto_login_failed", stderr=result.stderr.strip())
        return False
    except FileNotFoundError:
        log.warning(
            "wallet.onchainos_not_found",
            path=settings.onchainos_bin,
            hint="Install onchainos CLI or set ONCHAINOS_BIN env var to the correct path",
        )
        return False
    except Exception as e:
        log.warning("wallet.auto_login_error", error=str(e))
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    # Write OKX creds to ~/.onchainos/.env so the CLI can also read them from disk
    settings.write_onchainos_env()
    # Auto-login wallet on every startup — required for fresh Railway containers
    # where session files don't persist between deploys.
    # AK-mode login is non-interactive: uses OKX_API_KEY + SECRET + PASSPHRASE.
    import asyncio
    wallet_ok = await asyncio.get_event_loop().run_in_executor(None, _auto_login_wallet, settings)
    log.info(
        "purposa.startup",
        env=settings.purposa_env,
        okx_configured=settings.has_okx_credentials,
        llm_available=settings.has_llm_credentials,
        wallet_session_established=wallet_ok,
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

    # Landing page served before API routes so "/" → index.html
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        @app.get("/", include_in_schema=False)
        async def landing():
            return FileResponse(static_dir / "index.html")

        app.mount("/static", StaticFiles(directory=static_dir), name="static")

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
