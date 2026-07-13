"""Tool execution sandbox — workspace confinement, shell policy, per-runner rate limits."""

from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


# Path-like tool argument names inspected for confinement.
_PATH_ARG_KEYS = frozenset({
    "path", "cwd", "directory", "dest", "destination", "file", "dir",
})

# Tools whose `command` arg is PowerShell — must pass shell blocklist/allowlist.
_SHELL_COMMAND_TOOLS = frozenset({
    "run_command", "run_command_live", "run_powershell",
})

# Default dangerous PowerShell / shell patterns (case-insensitive).
_DEFAULT_SHELL_BLOCKLIST = [
    r"Format-Volume\b",
    r"Clear-Disk\b",
    r"Initialize-Disk\b",
    r"Remove-Partition\b",
    r"Stop-Computer\b",
    r"Restart-Computer\b",
    r"Reset-ComputerMachinePassword\b",
    r"\bshutdown\b",
    r"\bcipher\s+/w",
    r"Remove-Item\s+.*-(Recurse|r|Force).*(Windows|System32|WinSxS|Program\s*Files)",
    r"(rm|del)\s+(-rf|/s|/q).*(Windows|System32)",
    r"\breg\s+(delete|add)\b",
    r"\bnet\s+user\b.*/(add|delete|active)",
    r"Invoke-Expression\s*\(\s*(Invoke-WebRequest|iwr|curl|wget)",
    r"\biex\s*\(\s*(Invoke-WebRequest|iwr|curl|wget)",
    r"DownloadString\s*\(.*\|\s*iex",
    r"Set-ExecutionPolicy\s+.*(Unrestricted|Bypass)",
    r"Disable-WindowsDefender|Set-MpPreference.*Disable",
    r"New-Service\b",
    r"sc\.exe\s+(create|delete)",
]


class RateLimiter:
    """Sliding-window counter keyed by runner id."""

    def __init__(self, window_seconds: float, max_calls: int):
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_calls = max(1, int(max_calls))
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, runner_id: str) -> str | None:
        """Return an error message if over limit, else None."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[runner_id]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.max_calls:
                retry = max(1, int(self.window_seconds - (now - q[0])) + 1)
                return (
                    f"Rate limit exceeded for runner '{runner_id}': "
                    f"{self.max_calls} calls / {int(self.window_seconds)}s. "
                    f"Retry in ~{retry}s."
                )
            return None

    def record(self, runner_id: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[runner_id]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            q.append(now)

    def remaining(self, runner_id: str) -> int:
        now = time.monotonic()
        with self._lock:
            q = self._hits[runner_id]
            cutoff = now - self.window_seconds
            while q and q[0] < cutoff:
                q.popleft()
            return max(0, self.max_calls - len(q))


class ToolPolicy:
    """Config-driven gate for ToolRegistry.execute and optional LLM bursts."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        allowed_roots: list[Path] | None = None,
        shell_mode: str = "blocklist",
        shell_blocklist: list[str] | None = None,
        shell_allowlist: list[str] | None = None,
        window_seconds: float = 60,
        max_tool_calls: int = 120,
        max_llm_calls: int = 40,
        runner_id: str | None = None,
    ):
        self.enabled = bool(enabled)
        self.allowed_roots = [self._normalize_root(p) for p in (allowed_roots or [])]
        self.shell_mode = (shell_mode or "blocklist").strip().lower()
        raw_block = shell_blocklist if shell_blocklist is not None else _DEFAULT_SHELL_BLOCKLIST
        self._block_patterns = [re.compile(p, re.IGNORECASE) for p in raw_block if p]
        self._allow_patterns = [
            re.compile(p, re.IGNORECASE) for p in (shell_allowlist or []) if p
        ]
        self.tool_limiter = RateLimiter(window_seconds, max_tool_calls)
        self.llm_limiter = RateLimiter(window_seconds, max_llm_calls)
        self.default_runner_id = runner_id or "local"

    @staticmethod
    def _normalize_root(path: Path | str) -> Path:
        return Path(path).expanduser().resolve()

    @classmethod
    def from_config(
        cls,
        config: dict[str, Any] | None,
        *,
        workspace: str | Path,
        runner_id: str | None = None,
    ) -> "ToolPolicy":
        cfg = (config or {}).get("tool_policy") or {}
        paths = (config or {}).get("paths") or {}
        portfolio = (config or {}).get("portfolio") or {}

        roots: list[Path] = []
        for raw in cfg.get("allowed_roots") or []:
            roots.append(Path(raw))
        if not roots:
            ws = Path(workspace or paths.get("workspace") or ".")
            roots.append(ws)
            engine_root = portfolio.get("engine_root")
            if engine_root:
                roots.append(Path(engine_root))

        rate = cfg.get("rate_limits") or {}
        shell = cfg.get("shell") or {}
        # Empty/omitted blocklist → built-in defaults; non-empty entries are appended.
        configured_block = shell.get("blocklist")
        if configured_block:
            blocklist = list(_DEFAULT_SHELL_BLOCKLIST) + list(configured_block)
        else:
            blocklist = None  # ToolPolicy uses defaults
        return cls(
            enabled=cfg.get("enabled", True),
            allowed_roots=roots,
            shell_mode=shell.get("mode", "blocklist"),
            shell_blocklist=blocklist,
            shell_allowlist=shell.get("allowlist"),
            window_seconds=rate.get("window_seconds", 60),
            max_tool_calls=rate.get("max_tool_calls", 120),
            max_llm_calls=rate.get("max_llm_calls", 40),
            runner_id=runner_id,
        )

    def resolve_runner(self, runner_id: str | None = None) -> str:
        if runner_id:
            return runner_id
        try:
            from core.job_context import get_runner_id
            ctx = get_runner_id()
            if ctx:
                return ctx
        except Exception:
            pass
        return self.default_runner_id

    def resolve_path(self, path: str | Path, workspace: Path) -> Path:
        p = Path(path) if Path(path).is_absolute() else workspace / path
        try:
            return p.expanduser().resolve()
        except OSError:
            return p.expanduser().absolute()

    def is_under_roots(self, path: Path) -> bool:
        if not self.allowed_roots:
            return True
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def check_path(self, path: str | Path, workspace: Path) -> str | None:
        resolved = self.resolve_path(path, workspace)
        if self.is_under_roots(resolved):
            return None
        roots = ", ".join(str(r) for r in self.allowed_roots)
        return (
            f"Path denied by tool sandbox: '{resolved}' is outside allowed roots "
            f"[{roots}]"
        )

    def check_shell_command(self, command: str) -> str | None:
        cmd = (command or "").strip()
        if not cmd:
            return "Empty shell command denied by tool sandbox"

        if self.shell_mode == "allowlist":
            if not self._allow_patterns:
                return "Shell allowlist is empty — all commands denied"
            if not any(p.search(cmd) for p in self._allow_patterns):
                return f"Shell command denied (allowlist): {cmd[:200]}"
            return None

        # blocklist (default) — safer for an agent that needs flexible PowerShell
        for pattern in self._block_patterns:
            if pattern.search(cmd):
                return (
                    f"Shell command blocked by policy (matched /{pattern.pattern}/): "
                    f"{cmd[:200]}"
                )
        return None

    def check_tool(self, name: str, args: dict, workspace: Path,
                   runner_id: str | None = None) -> str | None:
        """Pre-execution gate. Returns error string or None if allowed."""
        if not self.enabled:
            return None

        rid = self.resolve_runner(runner_id)
        limited = self.tool_limiter.check(rid)
        if limited:
            return limited

        if name in ("run_command",) or "command" in (args or {}):
            if name == "run_command" or (
                name in ("run_powershell",) and "command" in args
            ):
                blocked = self.check_shell_command(str(args.get("command", "")))
                if blocked:
                    return blocked

        for key, value in (args or {}).items():
            if key not in _PATH_ARG_KEYS:
                continue
            if value is None or value == "":
                continue
            denied = self.check_path(str(value), workspace)
            if denied:
                return denied

        return None

    def record_tool(self, runner_id: str | None = None) -> None:
        if not self.enabled:
            return
        self.tool_limiter.record(self.resolve_runner(runner_id))

    def check_llm(self, runner_id: str | None = None) -> str | None:
        if not self.enabled:
            return None
        return self.llm_limiter.check(self.resolve_runner(runner_id))

    def record_llm(self, runner_id: str | None = None) -> None:
        if not self.enabled:
            return
        self.llm_limiter.record(self.resolve_runner(runner_id))

    def status(self, runner_id: str | None = None) -> dict[str, Any]:
        rid = self.resolve_runner(runner_id)
        return {
            "enabled": self.enabled,
            "runner_id": rid,
            "allowed_roots": [str(r) for r in self.allowed_roots],
            "shell_mode": self.shell_mode,
            "tool_calls_remaining": self.tool_limiter.remaining(rid),
            "llm_calls_remaining": self.llm_limiter.remaining(rid),
            "window_seconds": self.tool_limiter.window_seconds,
            "max_tool_calls": self.tool_limiter.max_calls,
            "max_llm_calls": self.llm_limiter.max_calls,
        }


class PolicyGuardedLLM:
    """Thin LLM wrapper that applies per-runner LLM burst limits."""

    def __init__(self, inner: Any, policy: ToolPolicy):
        self._inner = inner
        self._policy = policy

    def chat(self, messages: list[dict], temperature: float = 0.3) -> str:
        err = self._policy.check_llm()
        if err:
            raise RuntimeError(err)
        self._policy.record_llm()
        return self._inner.chat(messages, temperature=temperature)

    def is_available(self) -> bool:
        return self._inner.is_available()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
