"""Slack bot adapter for Ford Platform Agent.

Thin adapter that bridges Slack events to the headless Chat API (chat.py).
No AI logic lives here — all intelligence is in the Chat API; this module
handles Slack-specific concerns: threading, message formatting, streaming UX.

Architecture:
  Slack Event → Bolt Handler → HTTP POST to Chat API → SSE parse → Slack reply

Supports:
  - App mentions (@platform-agent deploy my app)
  - Slash commands (/platform <query>)
  - Direct messages to the bot
  - Thread-based conversations (thread_ts → Chat API session_id)
  - Streaming UX: "Processing..." → progressive updates → final response

Usage:
  ford-agent slack                      # Socket Mode (dev, no public URL)
  ford-agent slack --mode http          # HTTP mode (production, needs public URL)
  python -m ford_platform_agent.slack_bot  # direct
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import TYPE_CHECKING

import httpx
import structlog
from pydantic_settings import BaseSettings
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

if TYPE_CHECKING:
    from slack_bolt.context.say import Say

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class SlackConfig(BaseSettings):
    model_config = {"env_prefix": "SLACK_", "env_file": ".env", "extra": "ignore"}

    bot_token: str = ""
    signing_secret: str = ""
    app_token: str = ""
    chat_api_url: str = "http://localhost:8080"
    chat_api_key: str = ""
    update_interval_sec: float = 1.5
    request_timeout_sec: float = 120.0


# ---------------------------------------------------------------------------
# SSE stream parser
# ---------------------------------------------------------------------------


class SSEEvent:
    __slots__ = ("event_type", "data")

    def __init__(self, event_type: str, data: dict) -> None:
        self.event_type = event_type
        self.data = data


def parse_sse_stream(raw: str) -> list[SSEEvent]:
    events: list[SSEEvent] = []
    current_event = ""
    current_data = ""

    for line in raw.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:].strip()
        elif line.startswith("data: "):
            current_data = line[6:].strip()
        elif line == "" and current_event and current_data:
            try:
                events.append(SSEEvent(current_event, json.loads(current_data)))
            except json.JSONDecodeError:
                logger.warning("sse_parse_error", raw_data=current_data)
            current_event = ""
            current_data = ""

    return events


# ---------------------------------------------------------------------------
# Slack message formatter
# ---------------------------------------------------------------------------


def format_tool_call(tool_name: str, args: dict) -> str:
    args_summary = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""
    return f":wrench: `{tool_name}({args_summary})`"


def format_error(message: str) -> str:
    return f":warning: Something went wrong: {message}"


def assemble_slack_message(events: list[SSEEvent]) -> str:
    parts: list[str] = []
    tool_calls: list[str] = []
    text_chunks: list[str] = []

    for event in events:
        if event.event_type == "text.delta":
            text_chunks.append(event.data.get("text", ""))
        elif event.event_type == "tool.call":
            tool_calls.append(
                format_tool_call(
                    event.data.get("tool", "unknown"),
                    event.data.get("args", {}),
                )
            )
        elif event.event_type == "error":
            parts.append(format_error(event.data.get("message", "Unknown error")))
        elif event.event_type == "turn.complete":
            duration_ms = event.data.get("duration_ms", 0)
            if duration_ms > 0:
                parts.append(f"\n_Completed in {duration_ms / 1000:.1f}s_")

    if tool_calls:
        parts.insert(0, "\n".join(tool_calls) + "\n")
    if text_chunks:
        combined = "".join(text_chunks)
        if tool_calls:
            parts.insert(1, combined)
        else:
            parts.insert(0, combined)

    return "\n".join(parts).strip() or "I processed your request but have no response to show."


# ---------------------------------------------------------------------------
# Session mapper — maps Slack thread_ts to Chat API session_id
# ---------------------------------------------------------------------------


class SlackSessionMapper:
    def __init__(self) -> None:
        self._sessions: dict[str, str] = {}
        self._timestamps: dict[str, float] = {}

    def get_session(self, thread_key: str) -> str | None:
        return self._sessions.get(thread_key)

    def set_session(self, thread_key: str, session_id: str) -> None:
        self._sessions[thread_key] = session_id
        self._timestamps[thread_key] = time.monotonic()

    def remove_session(self, thread_key: str) -> None:
        self._sessions.pop(thread_key, None)
        self._timestamps.pop(thread_key, None)

    def cleanup_stale(self, max_age_hours: int = 24) -> int:
        now = time.monotonic()
        cutoff = max_age_hours * 3600
        stale = [k for k, ts in self._timestamps.items() if now - ts > cutoff]
        for key in stale:
            self.remove_session(key)
        return len(stale)

    @property
    def active_count(self) -> int:
        return len(self._sessions)

    @staticmethod
    def make_thread_key(channel: str, thread_ts: str) -> str:
        return f"{channel}:{thread_ts}"


# ---------------------------------------------------------------------------
# Chat API client
# ---------------------------------------------------------------------------


class ChatAPIClient:
    def __init__(self, config: SlackConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.chat_api_url,
            timeout=httpx.Timeout(config.request_timeout_sec, connect=10.0),
        )

    def _headers(self, user_id: str) -> dict[str, str]:
        headers: dict[str, str] = {"X-User-Id": user_id}
        if self._config.chat_api_key:
            headers["X-API-Key"] = self._config.chat_api_key
        return headers

    async def create_session(self, user_id: str) -> str:
        resp = await self._http.post(
            "/api/sessions",
            json={"user_id": user_id},
            headers=self._headers(user_id),
        )
        resp.raise_for_status()
        return resp.json()["session_id"]

    async def send_message(self, session_id: str, message: str, user_id: str) -> list[SSEEvent]:
        resp = await self._http.post(
            "/api/chat",
            json={"session_id": session_id, "message": message},
            headers=self._headers(user_id),
        )
        resp.raise_for_status()
        return parse_sse_stream(resp.text)

    async def close(self) -> None:
        await self._http.aclose()


# ---------------------------------------------------------------------------
# Core message handler
# ---------------------------------------------------------------------------


def _extract_user_message(text: str, bot_user_id: str) -> str:
    cleaned = re.sub(rf"<@{re.escape(bot_user_id)}>", "", text).strip()
    return cleaned


async def _handle_message(
    *,
    text: str,
    user_id: str,
    channel: str,
    thread_ts: str,
    say: Say,
    bot_user_id: str,
    client: ChatAPIClient,
    mapper: SlackSessionMapper,
) -> None:
    message = _extract_user_message(text, bot_user_id)
    if not message:
        say(text="Please include a message after mentioning me.", thread_ts=thread_ts)
        return

    thinking_resp = say(text=":hourglass_flowing_sand: Processing...", thread_ts=thread_ts)
    thinking_ts = thinking_resp.get("ts", "") if isinstance(thinking_resp, dict) else ""

    thread_key = SlackSessionMapper.make_thread_key(channel, thread_ts)
    slack_user_id = f"slack:{user_id}"

    try:
        session_id = mapper.get_session(thread_key)
        if not session_id:
            session_id = await client.create_session(slack_user_id)
            mapper.set_session(thread_key, session_id)

        events = await client.send_message(session_id, message, slack_user_id)
        response_text = assemble_slack_message(events)

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            response_text = (
                ":no_entry: Rate limit reached. Please wait a moment before trying again."
            )
        else:
            logger.error(
                "chat_api_error",
                status=exc.response.status_code,
                body=exc.response.text,
            )
            response_text = format_error(f"Chat API returned {exc.response.status_code}")
    except httpx.TimeoutException:
        response_text = (
            ":clock1: Request timed out. The agent may be processing a complex operation. "
            "Try again in a moment."
        )
    except Exception as exc:
        logger.error("slack_handler_error", error=str(exc), exc_info=True)
        response_text = format_error(str(exc))

    if thinking_ts:
        try:
            say.client.chat_update(
                channel=channel,
                ts=thinking_ts,
                text=response_text,
            )
        except Exception:
            say(text=response_text, thread_ts=thread_ts)
    else:
        say(text=response_text, thread_ts=thread_ts)


# ---------------------------------------------------------------------------
# Bot factory
# ---------------------------------------------------------------------------


def create_slack_app(config: SlackConfig | None = None) -> tuple[App, SlackSessionMapper]:
    config = config or SlackConfig()

    app = App(
        token=config.bot_token,
        signing_secret=config.signing_secret,
    )

    api_client = ChatAPIClient(config)
    mapper = SlackSessionMapper()

    @app.event("app_mention")
    def handle_mention(event: dict, say: Say) -> None:
        user_id = event.get("user", "unknown")
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        logger.info(
            "audit",
            action="slack.mention",
            user_id=user_id,
            channel=channel,
        )

        asyncio.get_event_loop().run_until_complete(
            _handle_message(
                text=text,
                user_id=user_id,
                channel=channel,
                thread_ts=thread_ts,
                say=say,
                bot_user_id=app.client.auth_test()["user_id"],
                client=api_client,
                mapper=mapper,
            )
        )

    @app.event("message")
    def handle_dm(event: dict, say: Say) -> None:
        if event.get("channel_type") != "im":
            return
        if event.get("subtype"):
            return

        user_id = event.get("user", "unknown")
        text = event.get("text", "")
        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts", event.get("ts", ""))

        logger.info(
            "audit",
            action="slack.dm",
            user_id=user_id,
        )

        asyncio.get_event_loop().run_until_complete(
            _handle_message(
                text=text,
                user_id=user_id,
                channel=channel,
                thread_ts=thread_ts,
                say=say,
                bot_user_id="",
                client=api_client,
                mapper=mapper,
            )
        )

    @app.command("/platform")
    def handle_slash_command(ack, command: dict, say: Say) -> None:
        ack()

        user_id = command.get("user_id", "unknown")
        text = command.get("text", "")
        channel = command.get("channel_id", "")
        trigger_id = command.get("trigger_id", "")

        thread_ts = f"slash_{trigger_id}"

        logger.info(
            "audit",
            action="slack.slash_command",
            user_id=user_id,
            text_length=len(text),
        )

        asyncio.get_event_loop().run_until_complete(
            _handle_message(
                text=text,
                user_id=user_id,
                channel=channel,
                thread_ts=thread_ts,
                say=say,
                bot_user_id="",
                client=api_client,
                mapper=mapper,
            )
        )

    return app, mapper


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------


def start_slack_bot(mode: str = "socket") -> None:
    config = SlackConfig()

    if not config.bot_token:
        logger.error("SLACK_BOT_TOKEN is required")
        raise SystemExit(1)

    app, mapper = create_slack_app(config)

    logger.info(
        "slack_bot_starting",
        mode=mode,
        chat_api_url=config.chat_api_url,
    )

    if mode == "socket":
        if not config.app_token:
            logger.error("SLACK_APP_TOKEN is required for Socket Mode")
            raise SystemExit(1)
        handler = SocketModeHandler(app, config.app_token)
        handler.start()
    else:
        app.start(port=3000)


if __name__ == "__main__":
    start_slack_bot()
