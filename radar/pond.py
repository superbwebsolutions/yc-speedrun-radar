"""
Pond Protocol server (optional integration).

Implements Pond's agent contract (docs.joinpond.ai) as FastAPI endpoints,
so the bot can be registered on joinpond.ai/agent/create for health
monitoring and review. When Pond pings the agent, it can run an on-demand
sweep ("status", "poll now", or a free-form prompt) and returns the
result as markdown.

Disabled unless POND_ENABLED=true; the bot runs identically without it.
"""
from __future__ import annotations

import os
import re
import traceback

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from .config import load_settings

app = FastAPI(title="YC/Speedrun Radar — Pond Agent")


# === Core Pond Protocol: public manifest ================================

@app.get("/manifest")
def manifest():
    return {
        "protocol": "marketplace-agent",
        "protocol_version": "1.0",
        "agent_version": "1.0.0",
        "metadata": {
            "name": "YC / Speedrun Launch Radar",
            "short_description": (
                "Monitors YC & a16z Speedrun directories plus X and LinkedIn "
                "for new and pre-announcement company signals."
            ),
            "description": (
                "A persistent monitor that watches four sources — the YC "
                "directory, the a16z Speedrun directory, X/Twitter and "
                "LinkedIn — and posts Slack alerts when a new YC/Speedrun "
                "company is listed or when a founder announces their "
                "acceptance on social media before the official listing."
            ),
            "category": "research",
            "demo_materials": [],
            "key_features": (
                "Persistent stateful monitoring; ⚡ early founder-signal "
                "detection before official announcements; X + LinkedIn "
                "keyword search; dedup via SQLite; Slack alerts; pluggable "
                "adapters for more platforms."
            ),
            "use_cases": (
                "GTM and sales teams who want to reach YC/Speedrun founders "
                "before anyone else; market research on new batches."
            ),
        },
        "actions": [
            {
                "id": "run_radar_check",
                "name": "Run Radar Check",
                "description": (
                    "Trigger an on-demand sweep of all four sources "
                    "(or ask for status). Interprets the prompt: "
                    "'status' returns monitor state, anything else runs "
                    "a poll cycle now."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "'status' for monitor state, or any text "
                                "to trigger an immediate poll cycle."
                            ),
                            "minLength": 1,
                        }
                    },
                    "required": ["prompt"],
                    "additionalProperties": False,
                },
            }
        ],
        "capabilities": {
            "sync": True,
            "streaming": False,
            "async_tasks": False,
            "cancellation": False,
            "attachments": False,
            "feedback": False,
        },
        "input_modes": ["text/plain"],
        "output_modes": ["text/markdown"],
        "limits": {
            "max_request_bytes": 1_048_576,
            "max_run_seconds": 300,
        },
    }


# === Pond Protocol: prepared run request =================================

class RunRequest(BaseModel):
    run_id: str
    agent_id: str
    conversation_id: str
    history_truncated: bool
    action_id: str | None = None
    user: dict
    messages: list[dict]
    parameters: dict
    execution: dict


# === Your Agent logic ====================================================

def run_agent(prompt: str) -> tuple[str | None, str | None]:
    """Interpret a Pond request: status report or on-demand sweep."""
    from .poller import cycle, log, make_sender

    settings = load_settings()
    conn = db.connect(settings.db_path)

    if prompt.strip().lower() in ("status", "report", "how are you?"):
        s = db.stats(conn)
        lines = [
            "## Radar status",
            "",
            f"- Companies tracked: **{s['companies_total']}** "
            f"(YC: {s['companies_yc']}, Speedrun: {s['companies_speedrun']})",
            f"- Social posts seen: **{s['signals_seen']}** "
            f"(early signals detected: **{s['signals_early']}**)",
            f"- Alerts delivered to Slack: **{s['alerts_sent']}**",
            f"- Last full cycle: {db.get_meta(conn, 'last_cycle') or 'never'}",
            f"- Next scheduled cycle: every "
            f"{settings.poll_interval_seconds/3600:.0f}h",
        ]
        return "\n".join(lines), None

    if "status" in prompt.lower()[:20]:
        return run_agent("status")

    # Default: run a poll cycle on demand.
    try:
        settings = load_settings()
        conn2 = db.connect(settings.db_path)
        send = make_sender(settings, conn2)
        counts = cycle(settings, conn2, send)
        lines = ["## Radar sweep complete", ""]
        for k, v in counts.items():
            lines.append(f"- **{k}**: {v}")
        return "\n".join(lines), None
    except Exception:
        traceback.print_exc()
    return None, "Radar sweep failed: see server logs for details."


# === Runtime authentication =============================================

def authenticate_pond(
    authorization: str | None = Header(default=None),
    pond_version: str | None = Header(
        default=None, alias="X-Agent-Protocol-Version"
    ),
):
    key = os.environ.get("POND_ACCESS_KEY", "")
    if not key:
        # No key configured → refuse runtime calls (manifest stays public).
        fail(401, "unauthorized", "POND_ACCESS_KEY is not configured.")
    if authorization != f"Bearer {key}":
        fail(401, "unauthorized", "The Access Key is missing or invalid.")
    if pond_version is None or re.fullmatch(r"\d+\.\d+", pond_version) is None:
        fail(400, "invalid_request", "The protocol version must be Major.Minor.")
    if pond_version != "1.0":
        fail(400, "unsupported_protocol_version",
             f"Protocol version {pond_version} is not supported.")


# === Core Pond Protocol: run endpoint ====================================

@app.post("/runs", dependencies=[Depends(authenticate_pond)])
async def create_run(
    run: RunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if idempotency_key != run.run_id:
        fail(400, "invalid_request", "Idempotency-Key must match run_id.")
    if run.action_id not in (None, "run_radar_check"):
        fail(400, "unsupported_operation", "The action is not supported.")

    prompt = run.parameters.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        fail(400, "invalid_request", "A non-empty prompt is required.")

    result, agent_error = run_agent(prompt)
    if agent_error:
        return {
            "run_id": run.run_id,
            "status": "failed",
            "error": {"code": "internal_error", "message": agent_error},
            "usage": {"unit_of_measurement": "result", "quantity": 0},
        }
    return {
        "run_id": run.run_id,
        "status": "completed",
        "output": [{"type": "text", "text": result}],
        "usage": {"unit_of_measurement": "result", "quantity": 1},
    }


# === Supporting: Pond error responses ====================================

from typing import NoReturn  # noqa: E402


def fail(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


@app.exception_handler(HTTPException)
async def pond_error(_request: Request, error: HTTPException):
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error.detail},
    )


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, _error: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "invalid_request",
                "message": "The request does not match Pond Protocol V1.",
            }
        },
    )
