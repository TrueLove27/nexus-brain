"""Scrub host secrets from env inherited by tool shell/code runners.

Child processes spawned by tools (PowerShell, live terminals, editors) must not
receive the host process's API keys, tokens, or passwords. Scrubbing happens at
the subprocess boundary — the Nexus parent still keeps its own environ.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from core.secret_redact import is_sensitive_key

# Exact env names always stripped (case-insensitive), even if not matched by key heuristics.
_ALWAYS_DENY = frozenset({
    "CURSOR_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "GIT_ASKPASS",
    "SSH_AUTH_SOCK",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AZURE_CLIENT_SECRET",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
    "PGPASSWORD",
    "MYSQL_PWD",
    "MONGODB_URI",
    "REDIS_URL",
    "NPM_TOKEN",
    "NODE_AUTH_TOKEN",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "WANDB_API_KEY",
    "SLACK_BOT_TOKEN",
    "DISCORD_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "NEXUS_DB_PASSWORD",
    "OLLAMA_API_KEY",
})

# Keep process-essential vars even when names look noisy (never strip these).
_ALWAYS_KEEP = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)", "USERNAME", "USERDOMAIN", "USERDOMAIN_ROAMINGPROFILE",
    "COMPUTERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER", "OS", "HOMEDRIVE", "HOMEPATH", "PUBLIC",
    "PSMODULEPATH", "POWERSHELL_DISTRIBUTION_CHANNEL",
    "TERM", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8",
    "ALLUSERSPROFILE", "DRIVERDATA", "SESSIONNAME", "LOGONSERVER",
})

# Value patterns that look like embedded secrets even under benign keys.
_SECRET_VALUE = re.compile(
    r"(?i)^(ghp_|gho_|ghu_|ghs_|github_pat_|sk-|sk-ant-|xox[baprs]-|AIza)"
)


class SubprocessEnvPolicy:
    """Build a scrubbed environ mapping for tool subprocesses."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        deny: list[str] | None = None,
        allow: list[str] | None = None,
    ):
        self.enabled = bool(enabled)
        self._deny = {n.upper() for n in (deny or []) if n}
        self._allow = {n.upper() for n in (allow or []) if n}
        self._deny |= {n.upper() for n in _ALWAYS_DENY}

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> "SubprocessEnvPolicy":
        cfg = ((config or {}).get("tool_policy") or {}).get("env_scrub") or {}
        return cls(
            enabled=cfg.get("enabled", True),
            deny=list(cfg.get("deny") or []),
            allow=list(cfg.get("allow") or []),
        )

    def should_strip(self, key: str, value: str | None = None) -> bool:
        upper = key.upper()
        if upper in self._allow or upper in _ALWAYS_KEEP:
            return False
        if upper in self._deny:
            return True
        if is_sensitive_key(key):
            return True
        # Soft suffixes commonly used for secrets in CI / local shells.
        if upper.endswith(("_TOKEN", "_SECRET", "_PASSWORD", "_PASSWD", "_API_KEY",
                           "_APIKEY", "_PRIVATE_KEY", "_ACCESS_KEY")):
            return True
        if value and _SECRET_VALUE.search(str(value).strip()):
            return True
        return False

    def scrub(
        self,
        source: Mapping[str, str] | None = None,
        *,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        """Return a copy of *source* (default os.environ) with secrets removed."""
        base = dict(source if source is not None else os.environ)
        if not self.enabled:
            out = base
        else:
            out = {k: v for k, v in base.items() if not self.should_strip(k, v)}
        if extra:
            for k, v in extra.items():
                if self.enabled and self.should_strip(k, v) and k.upper() not in self._allow:
                    continue
                out[k] = v
        return out

    def stripped_names(self, source: Mapping[str, str] | None = None) -> list[str]:
        """Names that would be removed (for health/status, not values)."""
        base = source if source is not None else os.environ
        if not self.enabled:
            return []
        return sorted(k for k, v in base.items() if self.should_strip(k, v))

    def status(self) -> dict[str, Any]:
        stripped = self.stripped_names()
        return {
            "enabled": self.enabled,
            "stripped_count": len(stripped),
            "stripped_names": stripped[:40],
            "deny_extras": sorted(self._deny - {n.upper() for n in _ALWAYS_DENY}),
            "allow": sorted(self._allow),
        }


_default_policy: SubprocessEnvPolicy | None = None


def get_env_policy() -> SubprocessEnvPolicy:
    global _default_policy
    if _default_policy is None:
        _default_policy = SubprocessEnvPolicy()
    return _default_policy


def set_env_policy(policy: SubprocessEnvPolicy | None) -> None:
    global _default_policy
    _default_policy = policy


def scrubbed_environ(
    extra: Mapping[str, str] | None = None,
    *,
    policy: SubprocessEnvPolicy | None = None,
) -> dict[str, str]:
    """Convenience: scrubbed os.environ (+ optional extra) for subprocess env=."""
    return (policy or get_env_policy()).scrub(extra=extra)
