"""Production-grade headless API server for Ford Platform Agent.

Powers ALL client interfaces (CLI, Slack bot, MCP, web) through a single
API with SSE streaming, session management, and enterprise middleware.

Architecture:
  Client → FastAPI → Auth → RateLimit → Audit → ADK Runner → SSE Stream
                                                    ↑
                                         Ford Knowledge + Guardrails

Endpoints:
  POST   /api/sessions          — Create a new conversation session
  GET    /api/sessions          — List user's active sessions
  GET    /api/sessions/{id}     — Get session info + conversation history
  DELETE /api/sessions/{id}     — End a session
  POST   /api/chat              — Send message, receive SSE stream
  GET    /health                — Liveness probe (Cloud Run)
  GET    /ready                 — Readiness probe (agent + providers)
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from ford_platform_agent.agent import build_agent
from ford_platform_agent.config import AgentConfig, ChatConfig

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from google.adk.runners import Runner

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Capability framing — tell developers what the agent can do upfront
# Research: "Obvious UX is premium; magical UX is dead" (2026 UX patterns)
# ---------------------------------------------------------------------------

CAPABILITIES = {
    "ci_cd": [
        "List recent pipeline runs for any repository",
        "Check build status on any branch",
        "Trigger CI/CD pipelines (GitHub Actions or Tekton)",
        "Cancel running pipelines",
    ],
    "source_control": [
        "List repositories in the organization",
        "View repository details and pull requests",
        "Create pull requests",
    ],
    "gitops": [
        "List ArgoCD-managed applications",
        "Check application sync and health status",
        "Trigger application sync (deploy)",
        "Rollback to a previous deployment",
        "View deployment history",
    ],
    "infrastructure": [
        "List environment configurations (dev/staging/prod)",
        "Provision new Cloud Run services via Terraform golden paths",
        "Read Terraform plan output from PRs",
        "Approve and merge infrastructure PRs (separation of duties enforced)",
    ],
    "project_scaffolding": [
        "List available project templates (Python/FastAPI, Node/Next.js, Java/Spring Boot)",
        "Create a new project from scratch — repo, code, CI, and infrastructure in one action",
        "Golden path templates include Dockerfile, health checks, tests, and CI workflows",
    ],
}

GUARDRAILS_INFO = {
    "production_requires_approval": True,
    "separation_of_duties": True,
    "rate_limit_enforced": True,
    "audit_trail": True,
    "knowledge_base": "Ford platform practices, deployment standards, guardrail rules",
}


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=256, description="Developer identity")
    metadata: dict = Field(default_factory=dict, description="Optional session metadata")


class CreateSessionResponse(BaseModel):
    session_id: str
    user_id: str
    capabilities: dict
    guardrails: dict
    created_at: str
    message: str


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=8000)


class SessionInfo(BaseModel):
    session_id: str
    user_id: str
    created_at: str
    last_active: str
    message_count: int


class ErrorResponse(BaseModel):
    error: str
    message: str
    request_id: str


# ---------------------------------------------------------------------------
# Session Manager — wraps ADK SessionService with metadata tracking
# ---------------------------------------------------------------------------


class SessionManager:
    """Manages conversation sessions with metadata beyond ADK's built-in tracking.

    Production swap: replace InMemorySessionService with DatabaseSessionService
    via a single config change — the SessionManager interface stays identical.
    """

    def __init__(self, config: ChatConfig) -> None:
        self._config = config
        self._metadata: dict[str, dict] = {}

    async def create(self, session_service, app_name: str, user_id: str) -> tuple[str, dict]:
        user_sessions = [sid for sid, meta in self._metadata.items() if meta["user_id"] == user_id]
        if len(user_sessions) >= self._config.max_sessions_per_user:
            raise ValueError(
                f"Max {self._config.max_sessions_per_user} sessions per user. "
                f"Delete an existing session first."
            )

        session = await session_service.create_session(app_name=app_name, user_id=user_id)
        now = datetime.now(UTC).isoformat()
        self._metadata[session.id] = {
            "user_id": user_id,
            "created_at": now,
            "last_active": now,
            "message_count": 0,
        }
        return session.id, self._metadata[session.id]

    def get_metadata(self, session_id: str, user_id: str) -> dict | None:
        meta = self._metadata.get(session_id)
        if not meta or meta["user_id"] != user_id:
            return None
        return meta

    def list_for_user(self, user_id: str) -> list[dict]:
        return [
            {"session_id": sid, **meta}
            for sid, meta in self._metadata.items()
            if meta["user_id"] == user_id
        ]

    def record_message(self, session_id: str) -> None:
        if session_id in self._metadata:
            self._metadata[session_id]["message_count"] += 1
            self._metadata[session_id]["last_active"] = datetime.now(UTC).isoformat()

    def delete(self, session_id: str, user_id: str) -> bool:
        meta = self._metadata.get(session_id)
        if not meta or meta["user_id"] != user_id:
            return False
        del self._metadata[session_id]
        return True

    def cleanup_expired(self) -> int:
        """Remove sessions older than TTL. Call periodically."""
        now = datetime.now(UTC)
        ttl_seconds = self._config.session_ttl_hours * 3600
        expired = []
        for sid, meta in self._metadata.items():
            created = datetime.fromisoformat(meta["created_at"])
            if (now - created).total_seconds() > ttl_seconds:
                expired.append(sid)
        for sid in expired:
            del self._metadata[sid]
        return len(expired)


# ---------------------------------------------------------------------------
# Per-user rate limiter — token bucket
# ---------------------------------------------------------------------------


class UserRateLimiter:
    def __init__(self, max_per_minute: int = 30) -> None:
        self._max = max_per_minute
        self._buckets: dict[str, list[float]] = {}

    def check(self, user_id: str) -> bool:
        now = time.monotonic()
        bucket = self._buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < 60]
        if len(bucket) >= self._max:
            return False
        bucket.append(now)
        return True

    def retry_after(self, user_id: str) -> int:
        bucket = self._buckets.get(user_id)
        if not bucket:
            return 0
        oldest = min(bucket)
        return max(1, int(60 - (time.monotonic() - oldest)))


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


async def _stream_agent_response(
    runner: Runner,
    session_id: str,
    user_id: str,
    message: str,
    request_id: str,
) -> AsyncGenerator[str, None]:
    """Stream ADK agent execution as typed SSE events.

    Event types:
      turn.start        — agent processing begins
      text.delta        — incremental text from agent
      tool.call         — agent invoking a tool (name + args)
      tool.result       — tool execution result
      turn.complete     — agent finished (includes duration)
      error             — something went wrong
    """
    from google.genai.types import Content, Part

    turn_id = f"turn_{uuid.uuid4().hex[:12]}"
    start_time = time.monotonic()

    yield _sse_event("turn.start", {"turn_id": turn_id, "request_id": request_id})

    user_content = Content(role="user", parts=[Part(text=message)])

    try:
        async for event in runner.run_async(
            session_id=session_id,
            user_id=user_id,
            new_message=user_content,
        ):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    yield _sse_event("text.delta", {"text": part.text})
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    yield _sse_event(
                        "tool.call",
                        {
                            "tool": fc.name,
                            "args": dict(fc.args) if fc.args else {},
                        },
                    )
                elif hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    result = fr.response
                    yield _sse_event(
                        "tool.result",
                        {
                            "tool": fr.name,
                            "result": (
                                result if isinstance(result, dict) else {"value": str(result)}
                            ),
                        },
                    )
    except Exception as exc:
        logger.error("agent_error", error=str(exc), request_id=request_id)
        yield _sse_event("error", {"message": str(exc), "request_id": request_id})

    duration_ms = int((time.monotonic() - start_time) * 1000)
    yield _sse_event("turn.complete", {"turn_id": turn_id, "duration_ms": duration_ms})


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


def _auth_dependency(config: ChatConfig):
    """Factory for the auth dependency — validates API key, extracts user_id."""

    async def _get_user_id(request: Request) -> str:
        if not config.api_keys:
            return request.headers.get("X-User-Id", "dev-user")

        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "message": "Missing X-API-Key header.",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )

        valid_keys = {k.strip() for k in config.api_keys.split(",") if k.strip()}
        if api_key not in valid_keys:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "unauthorized",
                    "message": "Invalid API key.",
                    "request_id": getattr(request.state, "request_id", ""),
                },
            )

        return request.headers.get("X-User-Id", f"user-{api_key[:8]}")

    return _get_user_id


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id", f"req_{uuid.uuid4().hex[:12]}")
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    chat_config: ChatConfig | None = None,
    agent_config: AgentConfig | None = None,
) -> FastAPI:
    """Create the production FastAPI app wrapping the ADK agent."""
    chat_config = chat_config or ChatConfig()
    agent_config = agent_config or AgentConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        agent, registry = build_agent(agent_config=agent_config)
        session_service = InMemorySessionService()
        runner = Runner(
            agent=agent,
            session_service=session_service,
            app_name=agent_config.app_name,
        )

        app.state.agent = agent
        app.state.runner = runner
        app.state.session_service = session_service
        app.state.registry = registry
        app.state.session_manager = SessionManager(chat_config)
        app.state.rate_limiter = UserRateLimiter(chat_config.rate_limit_per_minute)

        logger.info(
            "server_started",
            model=agent_config.model,
            app_name=agent_config.app_name,
        )

        yield

        await registry.close_all()
        logger.info("server_stopped")

    app = FastAPI(
        title="Ford Platform Agent API",
        description=(
            "Headless API for Ford's AI-powered platform engineering agent. "
            "Powers CLI, Slack bot, MCP, and any future interface."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    origins = [o.strip() for o in chat_config.cors_origins.split(",")]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)

    get_user_id = _auth_dependency(chat_config)

    # -----------------------------------------------------------------------
    # Health probes (no auth)
    # -----------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    async def health():
        return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}

    @app.get("/ready", tags=["ops"])
    async def ready(request: Request):
        checks = {"agent": app.state.agent is not None}
        available = app.state.registry.list_available()
        checks["providers"] = bool(available.get("ci") or available.get("gitops"))
        all_ok = all(checks.values())
        return JSONResponse(
            status_code=200 if all_ok else 503,
            content={
                "status": "ready" if all_ok else "degraded",
                "checks": checks,
                "providers": available,
            },
        )

    # -----------------------------------------------------------------------
    # Session endpoints
    # -----------------------------------------------------------------------

    @app.post(
        "/api/sessions",
        response_model=CreateSessionResponse,
        tags=["sessions"],
    )
    async def create_session(
        body: CreateSessionRequest,
        request: Request,
        user_id: str = Depends(get_user_id),
    ):
        effective_user = body.user_id or user_id
        mgr: SessionManager = app.state.session_manager

        try:
            session_id, meta = await mgr.create(
                app.state.session_service,
                app.state.agent.name,
                effective_user,
            )
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc

        logger.info(
            "audit",
            action="session.created",
            user_id=effective_user,
            session_id=session_id,
            request_id=request.state.request_id,
        )

        return CreateSessionResponse(
            session_id=session_id,
            user_id=effective_user,
            capabilities=CAPABILITIES,
            guardrails=GUARDRAILS_INFO,
            created_at=meta["created_at"],
            message=(
                "Platform Agent ready. I can manage your CI/CD pipelines, "
                "GitOps deployments, and infrastructure. What would you like to do?"
            ),
        )

    @app.get("/api/sessions", tags=["sessions"])
    async def list_sessions(user_id: str = Depends(get_user_id)):
        mgr: SessionManager = app.state.session_manager
        sessions = mgr.list_for_user(user_id)
        return {"sessions": sessions, "total": len(sessions)}

    @app.get("/api/sessions/{session_id}", tags=["sessions"])
    async def get_session(
        session_id: str,
        user_id: str = Depends(get_user_id),
    ):
        mgr: SessionManager = app.state.session_manager
        meta = mgr.get_metadata(session_id, user_id)
        if not meta:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "session_not_found",
                    "message": (
                        f"Session '{session_id}' not found. Create one via POST /api/sessions."
                    ),
                },
            )

        session = await app.state.session_service.get_session(
            app_name=app.state.agent.name,
            user_id=user_id,
            session_id=session_id,
        )

        history = []
        if session and session.events:
            for event in session.events:
                if not event.content or not event.content.parts:
                    continue
                entry = {"role": event.content.role, "parts": []}
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        entry["parts"].append({"type": "text", "text": part.text})
                    elif hasattr(part, "function_call") and part.function_call:
                        entry["parts"].append(
                            {
                                "type": "tool_call",
                                "tool": part.function_call.name,
                                "args": (
                                    dict(part.function_call.args) if part.function_call.args else {}
                                ),
                            }
                        )
                    elif hasattr(part, "function_response") and part.function_response:
                        entry["parts"].append(
                            {
                                "type": "tool_result",
                                "tool": part.function_response.name,
                            }
                        )
                if entry["parts"]:
                    history.append(entry)

        return {
            "session_id": session_id,
            **meta,
            "history": history,
        }

    @app.delete("/api/sessions/{session_id}", tags=["sessions"])
    async def delete_session(
        session_id: str,
        request: Request,
        user_id: str = Depends(get_user_id),
    ):
        mgr: SessionManager = app.state.session_manager
        deleted = mgr.delete(session_id, user_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "session_not_found",
                    "message": f"Session '{session_id}' not found.",
                },
            )

        logger.info(
            "audit",
            action="session.deleted",
            user_id=user_id,
            session_id=session_id,
            request_id=request.state.request_id,
        )
        return {"deleted": True, "session_id": session_id}

    # -----------------------------------------------------------------------
    # Chat endpoint — the core: message in, SSE stream out
    # -----------------------------------------------------------------------

    @app.post("/api/chat", tags=["chat"])
    async def chat(
        body: ChatRequest,
        request: Request,
        user_id: str = Depends(get_user_id),
    ):
        mgr: SessionManager = app.state.session_manager
        limiter: UserRateLimiter = app.state.rate_limiter

        meta = mgr.get_metadata(body.session_id, user_id)
        if not meta:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": "session_not_found",
                    "message": f"Session '{body.session_id}' not found.",
                    "request_id": request.state.request_id,
                },
            )

        if not limiter.check(user_id):
            retry = limiter.retry_after(user_id)
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "rate_limited",
                    "message": "Rate limit exceeded. Please wait before sending more messages.",
                    "retry_after_seconds": retry,
                    "request_id": request.state.request_id,
                },
            )

        mgr.record_message(body.session_id)

        logger.info(
            "audit",
            action="chat.message",
            user_id=user_id,
            session_id=body.session_id,
            message_length=len(body.message),
            request_id=request.state.request_id,
        )

        return StreamingResponse(
            _stream_agent_response(
                runner=app.state.runner,
                session_id=body.session_id,
                user_id=user_id,
                message=body.message,
                request_id=request.state.request_id,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Request-Id": request.state.request_id,
            },
        )

    return app


# ---------------------------------------------------------------------------
# Module-level app for `uvicorn ford_platform_agent.chat:app`
# ---------------------------------------------------------------------------

app = create_app()


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------


def start_server(
    host: str = "0.0.0.0",
    port: int = 8080,
    dev: bool = False,
) -> None:
    """Start the production chat server."""
    import uvicorn

    logger.info("starting_server", host=host, port=port, dev=dev)

    uvicorn.run(
        "ford_platform_agent.chat:app" if dev else app,
        host=host,
        port=port,
        reload=dev,
        log_level="info",
        access_log=True,
        timeout_keep_alive=300,
    )
