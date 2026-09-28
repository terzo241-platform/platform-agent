"""Tests for the Slack bot adapter — SSE parsing, session mapping, message formatting."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ford_platform_agent.slack_bot import (
    ChatAPIClient,
    SlackConfig,
    SlackSessionMapper,
    SSEEvent,
    _extract_user_message,
    assemble_slack_message,
    create_slack_app,
    format_error,
    format_tool_call,
    parse_sse_stream,
)

# ---------------------------------------------------------------------------
# SlackConfig
# ---------------------------------------------------------------------------


class TestSlackConfig:
    def test_defaults(self):
        config = SlackConfig()
        assert config.bot_token == ""
        assert config.signing_secret == ""
        assert config.app_token == ""
        assert config.chat_api_url == "http://localhost:8080"
        assert config.chat_api_key == ""
        assert config.update_interval_sec == 1.5
        assert config.request_timeout_sec == 120.0

    def test_env_prefix(self):
        assert SlackConfig.model_config["env_prefix"] == "SLACK_"

    def test_custom_values(self):
        config = SlackConfig(
            bot_token="xoxb-test",
            signing_secret="secret123",
            chat_api_url="http://chat:9090",
            chat_api_key="api-key-001",
        )
        assert config.bot_token == "xoxb-test"
        assert config.signing_secret == "secret123"
        assert config.chat_api_url == "http://chat:9090"
        assert config.chat_api_key == "api-key-001"


# ---------------------------------------------------------------------------
# SSE stream parsing
# ---------------------------------------------------------------------------


class TestSSEParsing:
    def test_parse_single_text_event(self):
        raw = 'event: text.delta\ndata: {"text": "hello"}\n\n'
        events = parse_sse_stream(raw)
        assert len(events) == 1
        assert events[0].event_type == "text.delta"
        assert events[0].data["text"] == "hello"

    def test_parse_multiple_events(self):
        raw = (
            'event: turn.start\ndata: {"turn_id": "t1", "request_id": "r1"}\n\n'
            'event: text.delta\ndata: {"text": "hello "}\n\n'
            'event: text.delta\ndata: {"text": "world"}\n\n'
            'event: turn.complete\ndata: {"turn_id": "t1", "duration_ms": 500}\n\n'
        )
        events = parse_sse_stream(raw)
        assert len(events) == 4
        assert events[0].event_type == "turn.start"
        assert events[1].event_type == "text.delta"
        assert events[2].event_type == "text.delta"
        assert events[3].event_type == "turn.complete"

    def test_parse_tool_call_event(self):
        raw = (
            'event: tool.call\n'
            'data: {"tool": "list_pipeline_runs", "args": {"repo": "my-app"}}\n\n'
        )
        events = parse_sse_stream(raw)
        assert len(events) == 1
        assert events[0].data["tool"] == "list_pipeline_runs"
        assert events[0].data["args"]["repo"] == "my-app"

    def test_parse_tool_result_event(self):
        raw = (
            'event: tool.result\n'
            'data: {"tool": "list_pipeline_runs", "result": {"runs": []}}\n\n'
        )
        events = parse_sse_stream(raw)
        assert len(events) == 1
        assert events[0].event_type == "tool.result"
        assert events[0].data["result"]["runs"] == []

    def test_parse_error_event(self):
        raw = 'event: error\ndata: {"message": "something broke", "request_id": "r1"}\n\n'
        events = parse_sse_stream(raw)
        assert len(events) == 1
        assert events[0].event_type == "error"
        assert events[0].data["message"] == "something broke"

    def test_parse_empty_string(self):
        events = parse_sse_stream("")
        assert events == []

    def test_parse_malformed_json_skipped(self):
        raw = "event: text.delta\ndata: {not json}\n\n"
        events = parse_sse_stream(raw)
        assert events == []

    def test_parse_ignores_incomplete_events(self):
        raw = "event: text.delta\n"
        events = parse_sse_stream(raw)
        assert events == []


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------


class TestFormatToolCall:
    def test_with_args(self):
        result = format_tool_call("list_pipeline_runs", {"repo": "my-app"})
        assert ":wrench:" in result
        assert "list_pipeline_runs" in result
        assert "repo=my-app" in result

    def test_without_args(self):
        result = format_tool_call("list_environments", {})
        assert "list_environments()" in result

    def test_multiple_args(self):
        result = format_tool_call("trigger_pipeline", {"repo": "app", "ref": "main"})
        assert "repo=app" in result
        assert "ref=main" in result


class TestFormatError:
    def test_error_format(self):
        result = format_error("Connection refused")
        assert ":warning:" in result
        assert "Connection refused" in result


class TestAssembleSlackMessage:
    def test_text_only(self):
        events = [
            SSEEvent("turn.start", {"turn_id": "t1", "request_id": "r1"}),
            SSEEvent("text.delta", {"text": "Hello "}),
            SSEEvent("text.delta", {"text": "world"}),
            SSEEvent("turn.complete", {"turn_id": "t1", "duration_ms": 250}),
        ]
        result = assemble_slack_message(events)
        assert "Hello world" in result
        assert "0.2s" in result

    def test_tool_call_then_text(self):
        events = [
            SSEEvent("tool.call", {"tool": "list_repos", "args": {}}),
            SSEEvent("tool.result", {"tool": "list_repos", "result": {}}),
            SSEEvent("text.delta", {"text": "Found 5 repos."}),
            SSEEvent("turn.complete", {"turn_id": "t1", "duration_ms": 800}),
        ]
        result = assemble_slack_message(events)
        assert ":wrench:" in result
        assert "list_repos" in result
        assert "Found 5 repos." in result

    def test_error_event(self):
        events = [
            SSEEvent("error", {"message": "Rate limit exceeded"}),
        ]
        result = assemble_slack_message(events)
        assert ":warning:" in result
        assert "Rate limit exceeded" in result

    def test_empty_events_fallback(self):
        events = [SSEEvent("turn.start", {"turn_id": "t1", "request_id": "r1"})]
        result = assemble_slack_message(events)
        assert "no response to show" in result

    def test_zero_duration_omitted(self):
        events = [
            SSEEvent("text.delta", {"text": "Hi"}),
            SSEEvent("turn.complete", {"turn_id": "t1", "duration_ms": 0}),
        ]
        result = assemble_slack_message(events)
        assert "Completed in" not in result

    def test_multiple_tool_calls(self):
        events = [
            SSEEvent("tool.call", {"tool": "get_pipeline_status", "args": {"repo": "app"}}),
            SSEEvent("tool.result", {"tool": "get_pipeline_status", "result": {}}),
            SSEEvent("tool.call", {"tool": "list_repositories", "args": {}}),
            SSEEvent("tool.result", {"tool": "list_repositories", "result": {}}),
            SSEEvent("text.delta", {"text": "Here are your results."}),
            SSEEvent("turn.complete", {"turn_id": "t1", "duration_ms": 1200}),
        ]
        result = assemble_slack_message(events)
        assert "get_pipeline_status" in result
        assert "list_repositories" in result
        assert "Here are your results." in result


# ---------------------------------------------------------------------------
# User message extraction
# ---------------------------------------------------------------------------


class TestExtractUserMessage:
    def test_strips_bot_mention(self):
        result = _extract_user_message("<@U12345> deploy my app", "U12345")
        assert result == "deploy my app"

    def test_no_mention(self):
        result = _extract_user_message("deploy my app", "U12345")
        assert result == "deploy my app"

    def test_empty_after_mention(self):
        result = _extract_user_message("<@U12345>", "U12345")
        assert result == ""

    def test_multiple_mentions(self):
        result = _extract_user_message("<@U12345> hey <@U12345> deploy", "U12345")
        assert "hey" in result
        assert "deploy" in result

    def test_empty_bot_id(self):
        result = _extract_user_message("deploy my app", "")
        assert result == "deploy my app"


# ---------------------------------------------------------------------------
# Session mapper
# ---------------------------------------------------------------------------


class TestSlackSessionMapper:
    @pytest.fixture
    def mapper(self):
        return SlackSessionMapper()

    def test_get_nonexistent(self, mapper):
        assert mapper.get_session("C001:ts001") is None

    def test_set_and_get(self, mapper):
        mapper.set_session("C001:ts001", "sess_abc")
        assert mapper.get_session("C001:ts001") == "sess_abc"

    def test_overwrite(self, mapper):
        mapper.set_session("C001:ts001", "sess_old")
        mapper.set_session("C001:ts001", "sess_new")
        assert mapper.get_session("C001:ts001") == "sess_new"

    def test_remove(self, mapper):
        mapper.set_session("C001:ts001", "sess_abc")
        mapper.remove_session("C001:ts001")
        assert mapper.get_session("C001:ts001") is None

    def test_remove_nonexistent_no_error(self, mapper):
        mapper.remove_session("nonexistent")

    def test_active_count(self, mapper):
        assert mapper.active_count == 0
        mapper.set_session("C001:ts001", "s1")
        mapper.set_session("C002:ts002", "s2")
        assert mapper.active_count == 2
        mapper.remove_session("C001:ts001")
        assert mapper.active_count == 1

    def test_make_thread_key(self):
        key = SlackSessionMapper.make_thread_key("C001", "1234567890.123456")
        assert key == "C001:1234567890.123456"

    def test_cleanup_stale_removes_old(self, mapper):
        mapper.set_session("C001:ts001", "s1")
        mapper._timestamps["C001:ts001"] = time.monotonic() - 90000
        mapper.set_session("C002:ts002", "s2")

        removed = mapper.cleanup_stale(max_age_hours=24)
        assert removed == 1
        assert mapper.get_session("C001:ts001") is None
        assert mapper.get_session("C002:ts002") == "s2"

    def test_cleanup_stale_keeps_recent(self, mapper):
        mapper.set_session("C001:ts001", "s1")
        removed = mapper.cleanup_stale(max_age_hours=24)
        assert removed == 0
        assert mapper.active_count == 1

    def test_independent_threads(self, mapper):
        mapper.set_session("C001:ts001", "s1")
        mapper.set_session("C001:ts002", "s2")
        mapper.set_session("C002:ts001", "s3")
        assert mapper.get_session("C001:ts001") == "s1"
        assert mapper.get_session("C001:ts002") == "s2"
        assert mapper.get_session("C002:ts001") == "s3"


# ---------------------------------------------------------------------------
# ChatAPIClient
# ---------------------------------------------------------------------------


class TestChatAPIClient:
    @pytest.fixture
    def config(self):
        return SlackConfig(
            chat_api_url="http://test:8080",
            chat_api_key="test-key",
        )

    def test_headers_with_api_key(self, config):
        client = ChatAPIClient(config)
        headers = client._headers("user1")
        assert headers["X-User-Id"] == "user1"
        assert headers["X-API-Key"] == "test-key"

    def test_headers_without_api_key(self):
        config = SlackConfig(chat_api_url="http://test:8080", chat_api_key="")
        client = ChatAPIClient(config)
        headers = client._headers("user1")
        assert "X-API-Key" not in headers

    @pytest.mark.asyncio
    async def test_create_session_calls_api(self, config):
        client = ChatAPIClient(config)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"session_id": "sess_123"}
        mock_resp.raise_for_status = MagicMock()
        client._http = AsyncMock()
        client._http.post = AsyncMock(return_value=mock_resp)

        session_id = await client.create_session("user1")
        assert session_id == "sess_123"
        client._http.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_parses_sse(self, config):
        client = ChatAPIClient(config)
        mock_resp = MagicMock()
        mock_resp.text = (
            'event: text.delta\ndata: {"text": "OK"}\n\n'
            'event: turn.complete\ndata: {"turn_id": "t1", "duration_ms": 100}\n\n'
        )
        mock_resp.raise_for_status = MagicMock()
        client._http = AsyncMock()
        client._http.post = AsyncMock(return_value=mock_resp)

        events = await client.send_message("sess_1", "hello", "user1")
        assert len(events) == 2
        assert events[0].event_type == "text.delta"
        assert events[1].event_type == "turn.complete"


# ---------------------------------------------------------------------------
# SSEEvent
# ---------------------------------------------------------------------------


class TestSSEEvent:
    def test_slots(self):
        event = SSEEvent("text.delta", {"text": "hi"})
        assert event.event_type == "text.delta"
        assert event.data == {"text": "hi"}
        with pytest.raises(AttributeError):
            event.nonexistent = True


# ---------------------------------------------------------------------------
# End-to-end message handling
# ---------------------------------------------------------------------------


class TestMessageHandling:
    def test_assemble_full_conversation(self):
        events = [
            SSEEvent("turn.start", {"turn_id": "t1", "request_id": "r1"}),
            SSEEvent("tool.call", {"tool": "list_pipeline_runs", "args": {"repo": "app"}}),
            SSEEvent(
                "tool.result",
                {
                    "tool": "list_pipeline_runs",
                    "result": {"runs": [{"id": 1, "status": "success"}]},
                },
            ),
            SSEEvent("text.delta", {"text": "Your latest pipeline "}),
            SSEEvent("text.delta", {"text": "run passed successfully!"}),
            SSEEvent("turn.complete", {"turn_id": "t1", "duration_ms": 1500}),
        ]
        result = assemble_slack_message(events)
        assert "list_pipeline_runs" in result
        assert "repo=app" in result
        assert "Your latest pipeline run passed successfully!" in result
        assert "1.5s" in result

    def test_assemble_error_only(self):
        events = [
            SSEEvent("turn.start", {"turn_id": "t1", "request_id": "r1"}),
            SSEEvent("error", {"message": "Agent timeout", "request_id": "r1"}),
        ]
        result = assemble_slack_message(events)
        assert "Agent timeout" in result
        assert ":warning:" in result


# ---------------------------------------------------------------------------
# Integration — create_slack_app factory
# ---------------------------------------------------------------------------


class TestCreateSlackApp:
    @patch("ford_platform_agent.slack_bot.App")
    def test_factory_returns_app_and_mapper(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app

        config = SlackConfig(bot_token="xoxb-test", signing_secret="secret")
        app, mapper = create_slack_app(config)

        assert app is mock_app
        assert isinstance(mapper, SlackSessionMapper)
        mock_app_cls.assert_called_once_with(
            token="xoxb-test",
            signing_secret="secret",
        )

    @patch("ford_platform_agent.slack_bot.App")
    def test_factory_registers_handlers(self, mock_app_cls):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app

        config = SlackConfig(bot_token="xoxb-test", signing_secret="secret")
        create_slack_app(config)

        event_calls = [call.args[0] for call in mock_app.event.call_args_list]
        assert "app_mention" in event_calls
        assert "message" in event_calls
        mock_app.command.assert_called_once_with("/platform")
