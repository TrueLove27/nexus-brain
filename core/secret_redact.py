"""Secret redaction for durable Postgres payloads.

Softens API keys, passwords, tokens, and similar secrets in strings and nested
JSON *before* tool_calls, checkpoints, and job_traces are written. In-memory /
LLM-facing data is left untouched — only the persistence boundary is scrubbed.
"""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"

# Common sensitive field names (case-insensitive; separators ignored).
_SENSITIVE_KEYS = frozenset({
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "access_token", "refresh_token", "id_token", "auth_token", "auth",
    "authorization", "bearer", "client_secret", "private_key", "privatekey",
    "ssh_key", "sshkey", "connection_string", "conn_str", "database_url",
    "db_url", "dsn", "credentials", "credential", "session_key", "sessionid",
    "session_id", "cookie", "csrf", "aws_secret_access_key", "aws_access_key_id",
    "github_token", "gh_token", "openai_api_key", "anthropic_api_key",
    "cursor_api_key", "ollama_api_key", "webhook_secret", "signing_key",
    "encryption_key", "hmac_key", "jwt", "jwt_secret", "bot_token",
})

# Value patterns that look like secrets even when the key is benign.
_VALUE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization / Bearer headers
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(bearer\s+)?([^\s\"']+)"), r"\1\2" + REDACTED),
    (re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9\-._~+/]+=*)"), r"\1" + REDACTED),
    # Explicit key/value assignments
    (re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
        r"secret[_-]?key|auth[_-]?token|password|passwd|pwd|github[_-]?token|"
        r"gh[_-]?token|cursor[_-]?api[_-]?key|openai[_-]?api[_-]?key|"
        r"aws[_-]?secret[_-]?access[_-]?key|private[_-]?key|dsn|database[_-]?url)"
        r"(\s*[=:]\s*)([^\s\"',;]+)"
    ), r"\1\2" + REDACTED),
    # JSON-ish "password": "..." (covers keys already walked, but text blobs too)
    (re.compile(
        r'(?i)("?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|'
        r'password|passwd|token|secret|authorization|private[_-]?key|dsn)"?'
        r'\s*:\s*)("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,}\]]+)'
    ), r"\1\"" + REDACTED + r"\""),
    # Known token shapes
    (re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"), REDACTED),  # GitHub PAT
    (re.compile(r"\bgho_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bghu_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bghs_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTED),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{20,}\b"), REDACTED),  # OpenAI-style
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), REDACTED),  # Slack
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"), REDACTED),  # Google API
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
    (re.compile(
        r"(?i)\b(postgresql|postgres|mysql|mongodb|redis|amqp|https?)://"
        r"[^\s\"']*:[^\s\"']+@[^\s\"']+"
    ), REDACTED),  # URLs with embedded credentials
    # PEM private key blocks
    (re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ), REDACTED),
]


def _normalize_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_sensitive_key(key: str) -> bool:
    """True if a dict key name looks like it holds a secret."""
    nk = _normalize_key(key)
    if nk in _SENSITIVE_KEYS:
        return True
    # Soft match: *token, *secret, *password, *apikey
    return bool(
        nk.endswith(("token", "secret", "password", "passwd", "apikey", "privatekey"))
        or nk.startswith(("password", "secret", "token"))
        or "apikey" in nk
        or "accesskey" in nk
    )


def redact_string(text: str) -> str:
    """Apply value-pattern redaction to free-form text."""
    if not text or not isinstance(text, str):
        return text if isinstance(text, str) else ""
    out = text
    for pattern, repl in _VALUE_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def redact_value(value: Any, *, key: str | None = None) -> Any:
    """Redact a single value; if *key* is sensitive, replace wholesale."""
    if key is not None and is_sensitive_key(key):
        if value is None or value == "":
            return value
        if isinstance(value, (dict, list)):
            return redact_structure(value)
        return REDACTED
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        return redact_structure(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_value(v) for v in value)
    return value


def redact_structure(obj: Any) -> Any:
    """Deep-copy-ish walk that redacts sensitive keys and secret-shaped strings."""
    if isinstance(obj, dict):
        return {k: redact_value(v, key=str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_value(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_value(v) for v in obj)
    if isinstance(obj, str):
        return redact_string(obj)
    return obj


def redact_tool_step(step: dict[str, Any] | None) -> dict[str, Any]:
    """Redact args/result/thought fields of a single tool/ReAct step."""
    if not isinstance(step, dict):
        return {}
    out = dict(step)
    if "args" in out:
        out["args"] = redact_structure(out.get("args") or {})
    if "result" in out and out["result"] is not None:
        out["result"] = redact_string(str(out["result"]))
    if "thought" in out and out["thought"] is not None:
        out["thought"] = redact_string(str(out["thought"]))
    # Catch secrets that leaked into action strings (rare).
    if "action" in out and isinstance(out["action"], str):
        out["action"] = redact_string(out["action"])
    return out


def redact_checkpoint(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Redact a durable ReAct checkpoint payload before jobs.checkpoint write."""
    if not isinstance(checkpoint, dict):
        return {}
    out = dict(checkpoint)
    steps = out.get("steps")
    if isinstance(steps, list):
        out["steps"] = [redact_tool_step(s) if isinstance(s, dict) else s for s in steps]
    if "transcript_summary" in out and out["transcript_summary"] is not None:
        out["transcript_summary"] = redact_string(str(out["transcript_summary"]))
    if "recent_actions" in out and isinstance(out["recent_actions"], list):
        out["recent_actions"] = [
            redact_string(str(a)) if a is not None else a for a in out["recent_actions"]
        ]
    # Agent id / counters are fine; still walk unknown nested dicts.
    for key, value in list(out.items()):
        if key in ("steps", "transcript_summary", "recent_actions", "version",
                   "next_iteration", "compacted_count", "parse_failures",
                   "wind_down_sent", "stuck_sent", "agent_id", "step_count"):
            continue
        out[key] = redact_value(value, key=str(key))
    return out
