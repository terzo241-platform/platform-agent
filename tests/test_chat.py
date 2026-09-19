"""Tests for the production chat server — auth, sessions, SSE streaming, rate limiting."""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from ford_platform_agent.chat import (
    CAPABILITIES,
    GUARDRAILS_INFO,
    SessionManager,
    UserRateLimiter,
    _sse_event,
    create_app,
)
from ford_platform_agent.config import ChatConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def chat_config():
    return ChatConfig(
        api_keys="",
        rate_limit_per_minute=30,
        max_sessions_per_user=3,
        session_ttl_hours=24,
    )


@pytest.fixture
def auth_config():
    return ChatConfig(
        api_keys="test-key-001,test-key-002",
        rate_limit_per_minute=30,
        max_sessions_per_user=3,
    )


def _mock_adk():
    """Mock ADK dependencies so we don't need actual Gemini credentials."""
    mock_agent = MagicMock()
    mock_agent.name = "ford_platform_agent"

    mock_registry = MagicMock()
    mock_registry.list_available.return_value = {"ci": ["github"], "gitops": ["argocd"]}
    mock_registry.close_all = AsyncMock()

    mock_session_service = MagicMock()
    mock_session = MagicMock()
    mock_session.id = "sess_test123"
    mock_session.events = []
    mock_session_service.create_session = AsyncMock(return_value=mock_session)
    mock_session_service.get_session = AsyncMock(return_value=mock_session)

    mock_runner = MagicMock()

    return mock_agent, mock_registry, mock_session_service, mock_runner


@pytest.fixture
def client(chat_config):
    """TestClient with mocked ADK (dev mode, no auth)."""
    app = create_app(chat_config=chat_config)
    agent, registry, session_svc, runner = _mock_adk()

    app.state.agent = agent
    app.state.runner = runner
    app.state.session_service = session_svc
    app.state.registry = registry
    app.state.session_manager = SessionManager(chat_config)
    app.state.rate_limiter = UserRateLimiter(chat_config.rate_limit_per_minute)

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def auth_client(auth_config):
    """TestClient with auth enabled."""
    app = create_app(chat_config=auth_config)
    agent, registry, session_svc, runner = _mock_adk()

    app.state.agent = agent
    app.state.runner = runner
    app.state.session_service = session_svc
    app.state.registry = registry
    app.state.session_manager = SessionManager(auth_config)
    app.state.rate_limiter = UserRateLimiter(auth_config.rate_limit_per_minute)

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Health probes
# ---------------------------------------------------------------------------


class TestHealthProbes:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "timestamp" in data

    def test_ready_returns_checks(self, client):
        resp = client.get("/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["checks"]["agent"] is True
        assert "providers" in data

    def test_health_has_request_id(self, client):
        resp = client.get("/health")
        assert "x-request-id" in resp.headers

    def test_custom_request_id_preserved(self, client):
        resp = client.get("/health", headers={"X-Request-Id": "my-req-001"})
        assert resp.headers["x-request-id"] == "my-req-001"


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------


class TestSessionCreation:
    def test_create_session_returns_capabilities(self, client):
        resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert "session_id" in data
        assert data["user_id"] == "dev@ford.com"
        assert data["capabilities"] == CAPABILITIES
        assert data["guardrails"] == GUARDRAILS_INFO
        assert "message" in data
        assert "created_at" in data

    def test_create_session_validates_user_id(self, client):
        resp = client.post("/api/sessions", json={"user_id": ""})
        assert resp.status_code == 422

    def test_max_sessions_per_user(self, client):
        for _ in range(3):
            resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
            assert resp.status_code == 200

        resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        assert resp.status_code == 429

    def test_different_users_independent_limits(self, client):
        for _ in range(3):
            client.post("/api/sessions", json={"user_id": "alice@ford.com"})
        resp = client.post("/api/sessions", json={"user_id": "bob@ford.com"})
        assert resp.status_code == 200


class TestSessionListing:
    def test_list_empty(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sessions"] == []
        assert data["total"] == 0

    def test_list_after_creation(self, client):
        client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        client.post("/api/sessions", json={"user_id": "dev@ford.com"})

        resp = client.get("/api/sessions", headers={"X-User-Id": "dev@ford.com"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2


class TestSessionDeletion:
    def test_delete_existing(self, client):
        create_resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        session_id = create_resp.json()["session_id"]

        resp = client.delete(
            f"/api/sessions/{session_id}",
            headers={"X-User-Id": "dev@ford.com"},
        )
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_nonexistent(self, client):
        resp = client.delete("/api/sessions/nonexistent")
        assert resp.status_code == 404

    def test_delete_frees_session_slot(self, client):
        sessions = []
        for _ in range(3):
            r = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
            sessions.append(r.json()["session_id"])

        resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        assert resp.status_code == 429

        client.delete(
            f"/api/sessions/{sessions[0]}",
            headers={"X-User-Id": "dev@ford.com"},
        )

        resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        assert resp.status_code == 200


class TestSessionInfo:
    def test_get_session_info(self, client):
        create_resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        session_id = create_resp.json()["session_id"]

        resp = client.get(
            f"/api/sessions/{session_id}",
            headers={"X-User-Id": "dev@ford.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == session_id
        assert data["user_id"] == "dev@ford.com"
        assert data["message_count"] == 0
        assert "history" in data

    def test_get_nonexistent_session(self, client):
        resp = client.get("/api/sessions/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "session_not_found"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_no_auth_in_dev_mode(self, client):
        resp = client.post("/api/sessions", json={"user_id": "anyone"})
        assert resp.status_code == 200

    def test_auth_required_with_api_keys(self, auth_client):
        resp = auth_client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        assert resp.status_code == 401

    def test_valid_api_key_accepted(self, auth_client):
        resp = auth_client.post(
            "/api/sessions",
            json={"user_id": "dev@ford.com"},
            headers={"X-API-Key": "test-key-001"},
        )
        assert resp.status_code == 200

    def test_invalid_api_key_rejected(self, auth_client):
        resp = auth_client.post(
            "/api/sessions",
            json={"user_id": "dev@ford.com"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_multiple_valid_keys(self, auth_client):
        resp1 = auth_client.post(
            "/api/sessions",
            json={"user_id": "dev@ford.com"},
            headers={"X-API-Key": "test-key-001"},
        )
        resp2 = auth_client.post(
            "/api/sessions",
            json={"user_id": "dev@ford.com"},
            headers={"X-API-Key": "test-key-002"},
        )
        assert resp1.status_code == 200
        assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def test_allows_under_limit(self):
        limiter = UserRateLimiter(max_per_minute=5)
        for _ in range(5):
            assert limiter.check("user1") is True

    def test_blocks_over_limit(self):
        limiter = UserRateLimiter(max_per_minute=2)
        assert limiter.check("user1") is True
        assert limiter.check("user1") is True
        assert limiter.check("user1") is False

    def test_per_user_isolation(self):
        limiter = UserRateLimiter(max_per_minute=1)
        assert limiter.check("user1") is True
        assert limiter.check("user1") is False
        assert limiter.check("user2") is True

    def test_retry_after_returns_positive(self):
        limiter = UserRateLimiter(max_per_minute=1)
        limiter.check("user1")
        limiter.check("user1")
        assert limiter.retry_after("user1") > 0

    def test_retry_after_zero_for_unknown(self):
        limiter = UserRateLimiter(max_per_minute=5)
        assert limiter.retry_after("unknown") == 0


# ---------------------------------------------------------------------------
# Session Manager
# ---------------------------------------------------------------------------


class TestSessionManager:
    @pytest.fixture
    def manager(self, chat_config):
        return SessionManager(chat_config)

    @pytest.fixture
    def mock_session_service(self):
        svc = MagicMock()
        session = MagicMock()
        session.id = "sess_001"
        svc.create_session = AsyncMock(return_value=session)
        return svc

    async def test_create_stores_metadata(self, manager, mock_session_service):
        session_id, meta = await manager.create(mock_session_service, "test-app", "dev@ford.com")
        assert session_id == "sess_001"
        assert meta["user_id"] == "dev@ford.com"
        assert meta["message_count"] == 0

    async def test_create_enforces_max_sessions(self, manager, mock_session_service):
        for idx in range(3):
            mock_session = MagicMock()
            mock_session.id = f"sess_{idx}"
            mock_session_service.create_session = AsyncMock(return_value=mock_session)
            await manager.create(mock_session_service, "test-app", "dev@ford.com")

        with pytest.raises(ValueError, match="Max 3 sessions"):
            await manager.create(mock_session_service, "test-app", "dev@ford.com")

    def test_get_metadata_wrong_user(self, manager):
        manager._metadata["sess_001"] = {
            "user_id": "alice",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        assert manager.get_metadata("sess_001", "bob") is None

    def test_record_message_increments(self, manager):
        manager._metadata["sess_001"] = {
            "user_id": "dev",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        manager.record_message("sess_001")
        assert manager._metadata["sess_001"]["message_count"] == 1

    def test_delete_returns_false_for_wrong_user(self, manager):
        manager._metadata["sess_001"] = {
            "user_id": "alice",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        assert manager.delete("sess_001", "bob") is False
        assert "sess_001" in manager._metadata

    def test_list_for_user_filters(self, manager):
        manager._metadata["s1"] = {
            "user_id": "alice",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        manager._metadata["s2"] = {
            "user_id": "bob",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        manager._metadata["s3"] = {
            "user_id": "alice",
            "created_at": "",
            "last_active": "",
            "message_count": 0,
        }
        result = manager.list_for_user("alice")
        assert len(result) == 2
        assert all(r["user_id"] == "alice" for r in result)


# ---------------------------------------------------------------------------
# SSE event formatting
# ---------------------------------------------------------------------------


class TestSSEEvents:
    def test_event_format(self):
        result = _sse_event("text.delta", {"text": "hello"})
        assert result.startswith("event: text.delta\n")
        assert "data: " in result
        assert result.endswith("\n\n")

    def test_event_json_valid(self):
        result = _sse_event("tool.call", {"tool": "list_repos", "args": {}})
        data_line = result.split("data: ")[1].strip()
        parsed = json.loads(data_line)
        assert parsed["tool"] == "list_repos"

    def test_event_types(self):
        for event_type in [
            "turn.start",
            "text.delta",
            "tool.call",
            "tool.result",
            "turn.complete",
            "error",
        ]:
            result = _sse_event(event_type, {"test": True})
            assert f"event: {event_type}\n" in result


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


class TestChatEndpoint:
    def test_chat_requires_valid_session(self, client):
        resp = client.post(
            "/api/chat",
            json={"session_id": "nonexistent", "message": "hello"},
        )
        assert resp.status_code == 404

    def test_chat_validates_empty_message(self, client):
        resp = client.post(
            "/api/chat",
            json={"session_id": "sess_001", "message": ""},
        )
        assert resp.status_code == 422

    def test_chat_returns_sse_content_type(self, client):
        create_resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        session_id = create_resp.json()["session_id"]

        async def mock_run_async(**kwargs):
            return
            yield  # make it an async generator that yields nothing

        client.app.state.runner.run_async = mock_run_async

        resp = client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "hello"},
            headers={"X-User-Id": "dev@ford.com"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]

    def test_chat_increments_message_count(self, client):
        create_resp = client.post("/api/sessions", json={"user_id": "dev@ford.com"})
        session_id = create_resp.json()["session_id"]

        async def mock_run_async(**kwargs):
            return
            yield

        client.app.state.runner.run_async = mock_run_async

        client.post(
            "/api/chat",
            json={"session_id": session_id, "message": "hello"},
            headers={"X-User-Id": "dev@ford.com"},
        )

        info = client.get(
            f"/api/sessions/{session_id}",
            headers={"X-User-Id": "dev@ford.com"},
        )
        assert info.json()["message_count"] == 1


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


class TestCLIIntegration:
    def test_chat_subcommand_exists(self):
        from ford_platform_agent.__main__ import parse_args

        old_argv = sys.argv
        sys.argv = ["ford-agent", "chat"]
        try:
            args = parse_args()
            assert args.command == "chat"
            assert args.port == 8080
            assert args.dev is False
        finally:
            sys.argv = old_argv

    def test_chat_dev_mode(self):
        from ford_platform_agent.__main__ import parse_args

        old_argv = sys.argv
        sys.argv = ["ford-agent", "chat", "--dev", "--port", "9090"]
        try:
            args = parse_args()
            assert args.dev is True
            assert args.port == 9090
        finally:
            sys.argv = old_argv


# ---------------------------------------------------------------------------
# ChatConfig
# ---------------------------------------------------------------------------


class TestChatConfig:
    def test_defaults(self):
        config = ChatConfig()
        assert config.host == "0.0.0.0"
        assert config.port == 8080
        assert config.cors_origins == "*"
        assert config.api_keys == ""
        assert config.session_ttl_hours == 24
        assert config.max_sessions_per_user == 10
        assert config.rate_limit_per_minute == 30

    def test_dev_mode_no_auth(self):
        config = ChatConfig(api_keys="")
        assert not config.api_keys

    def test_prod_mode_with_keys(self):
        config = ChatConfig(api_keys="key1,key2")
        keys = {k.strip() for k in config.api_keys.split(",") if k.strip()}
        assert len(keys) == 2


# ---------------------------------------------------------------------------
# OpenAPI docs
# ---------------------------------------------------------------------------


class TestOpenAPIDocs:
    def test_openapi_schema_available(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert schema["info"]["title"] == "Ford Platform Agent API"
        assert "/api/sessions" in schema["paths"]
        assert "/api/chat" in schema["paths"]
        assert "/health" in schema["paths"]
