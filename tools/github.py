"""GitHub operations via gh CLI - branch, commit, push."""

from __future__ import annotations

from pathlib import Path

from core.events import EventBus


def register_github_tools(registry) -> None:
    events = EventBus.get()

    def git_status(repo_path: str = "") -> str:
        cwd = repo_path or str(registry.workspace)
        return registry.run_powershell("git status -sb", cwd=cwd)

    def git_branch(branch_name: str, repo_path: str = "", create: bool = True) -> str:
        cwd = repo_path or str(registry.workspace)
        if create:
            cmd = f'git checkout -b "{branch_name}"'
        else:
            cmd = f'git checkout "{branch_name}"'
        events.emit("git_branch", {"branch": branch_name, "cwd": cwd})
        return registry.run_powershell(cmd, cwd=cwd)

    def git_commit(message: str, repo_path: str = "", add_all: bool = True) -> str:
        cwd = repo_path or str(registry.workspace)
        if add_all:
            registry.run_powershell("git add -A", cwd=cwd)
        safe_msg = message.replace('"', '`"')
        events.emit("git_commit", {"message": message, "cwd": cwd})
        return registry.run_powershell(f'git commit -m "{safe_msg}"', cwd=cwd)

    def git_push(repo_path: str = "", branch: str = "", set_upstream: bool = True) -> str:
        cwd = repo_path or str(registry.workspace)
        if branch and set_upstream:
            cmd = f'git push -u origin "{branch}"'
        elif branch:
            cmd = f'git push origin "{branch}"'
        else:
            cmd = "git push"
        events.emit("git_push", {"branch": branch, "cwd": cwd})
        return registry.run_powershell(cmd, cwd=cwd, timeout=180)

    def github_push_changes(
        message: str,
        repo_path: str = "",
        branch: str = "",
        create_branch: bool = False,
    ) -> str:
        """Create branch (optional), commit all changes, and push to origin."""
        cwd = repo_path or str(registry.workspace)
        parts = []

        if create_branch and branch:
            parts.append(git_branch(branch, cwd, create=True))
        elif branch:
            parts.append(git_branch(branch, cwd, create=False))

        parts.append(git_commit(message, cwd, add_all=True))
        parts.append(git_push(cwd, branch if branch else "", set_upstream=bool(branch)))
        return "\n---\n".join(parts)

    def github_create_pr(
        title: str,
        body: str = "",
        repo_path: str = "",
        base: str = "master",
    ) -> str:
        cwd = repo_path or str(registry.workspace)
        safe_title = title.replace('"', '`"')
        safe_body = body.replace('"', '`"')
        cmd = f'gh pr create --title "{safe_title}" --body "{safe_body}" --base {base}'
        events.emit("github_pr", {"title": title, "cwd": cwd})
        return registry.run_powershell(cmd, cwd=cwd, timeout=120)

    registry.register(
        "git_status",
        "Show git status for a repo",
        {"repo_path": "str (optional)"},
        git_status,
    )
    registry.register(
        "git_branch",
        "Create or switch to a git branch",
        {"branch_name": "str", "repo_path": "str (optional)", "create": "bool (default true)"},
        git_branch,
    )
    registry.register(
        "git_commit",
        "Stage and commit changes",
        {"message": "str", "repo_path": "str (optional)", "add_all": "bool (default true)"},
        git_commit,
    )
    registry.register(
        "git_push",
        "Push commits to origin",
        {"repo_path": "str (optional)", "branch": "str (optional)", "set_upstream": "bool"},
        git_push,
    )
    registry.register(
        "github_push_changes",
        "Branch, commit all changes, and push in one step",
        {
            "message": "str",
            "repo_path": "str (optional)",
            "branch": "str (optional)",
            "create_branch": "bool (default false)",
        },
        github_push_changes,
    )
    registry.register(
        "github_create_pr",
        "Create a GitHub pull request via gh CLI",
        {"title": "str", "body": "str (optional)", "repo_path": "str (optional)", "base": "str"},
        github_create_pr,
    )
