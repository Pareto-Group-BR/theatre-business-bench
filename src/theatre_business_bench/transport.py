from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class ModelTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelResult:
    content: dict[str, Any] | None
    text: str
    run_id: str
    session_id: str
    provider: str
    model: str
    duration_ms: int
    usage: dict[str, int]
    parse_error: str | None = None


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ModelTransportError("model response did not contain a JSON object")
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelTransportError(f"invalid model JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelTransportError("model response must be a JSON object")
    return value


class OpenClawCodexTransport:
    """Invoke Codex through OpenClaw's ChatGPT OAuth route, never an API key."""

    def __init__(
        self,
        agent_id: str = "business-bench",
        model: str = "openai/gpt-5.6-sol",
        thinking: str = "medium",
        timeout_seconds: int = 600,
    ):
        self.agent_id = agent_id
        self.model = model
        self.thinking = thinking
        self.timeout_seconds = timeout_seconds

    def invoke(self, session_key: str, message: str) -> ModelResult:
        command = [
            "openclaw", "agent",
            "--agent", self.agent_id,
            "--session-key", session_key,
            "--model", self.model,
            "--thinking", self.thinking,
            "--timeout", str(self.timeout_seconds),
            "--json",
            "--message", message,
        ]
        started = time.monotonic()
        pass_fds: tuple[int, ...] = ()
        lock_fd = os.environ.get("THEATRE_OFFICIAL_LOCK_FD")
        if lock_fd is not None:
            try:
                descriptor = int(lock_fd)
                os.fstat(descriptor)
            except (ValueError, OSError) as exc:
                raise ModelTransportError("official lock descriptor is invalid") from exc
            pass_fds = (descriptor,)
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds + 30,
            pass_fds=pass_fds,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if completed.returncode != 0:
            combined = (completed.stderr + "\n" + completed.stdout).strip()
            raise ModelTransportError(f"OpenClaw model call failed ({completed.returncode}): {combined[-1200:]}")
        try:
            envelope = json.loads(completed.stdout)
            result = envelope["result"]
            meta = result["meta"]["agentMeta"]
            text = result["payloads"][0]["text"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ModelTransportError("OpenClaw returned an unexpected JSON envelope") from exc
        usage_raw = meta.get("lastCallUsage") or meta.get("usage") or {}
        usage = {
            "input": int(usage_raw.get("input", 0)),
            "output": int(usage_raw.get("output", 0)),
            "cache_read": int(usage_raw.get("cacheRead", 0)),
            "cache_write": int(usage_raw.get("cacheWrite", 0)),
            "total": int(usage_raw.get("total", 0)),
        }
        try:
            content = parse_json_object(text)
            parse_error = None
        except ModelTransportError as exc:
            # The provider call already happened and its reported usage is
            # evidence. Return the intact envelope so the runner can persist
            # the failed response before stopping the experiment loud.
            content = None
            parse_error = str(exc)
        return ModelResult(
            content=content,
            text=text,
            run_id=str(envelope.get("runId", "")),
            session_id=str(meta.get("sessionId", "")),
            provider=str(meta.get("provider", "")),
            model=str(meta.get("model", "")),
            duration_ms=int(meta.get("durationMs", duration_ms)),
            usage=usage,
            parse_error=parse_error,
        )
