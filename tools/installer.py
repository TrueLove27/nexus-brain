from __future__ import annotations

from core.events import EventBus

# winget IDs for common free apps
WINGET_APPS = {
    "github desktop": "GitHub.GitHubDesktop",
    "githubdesktop": "GitHub.GitHubDesktop",
    "vscode": "Microsoft.VisualStudioCode",
    "visual studio code": "Microsoft.VisualStudioCode",
    "cursor": "Cursor.Cursor",
    "git": "Git.Git",
    "node": "OpenJS.NodeJS.LTS",
    "nodejs": "OpenJS.NodeJS.LTS",
    "python": "Python.Python.3.12",
    "docker": "Docker.DockerDesktop",
    "chrome": "Google.Chrome",
    "firefox": "Mozilla.Firefox",
    "notion": "Notion.Notion",
    "slack": "SlackTechnologies.Slack",
    "discord": "Discord.Discord",
    "spotify": "Spotify.Spotify",
    "obs": "OBSProject.OBSStudio",
    "postman": "Postman.Postman",
    "mongodb compass": "MongoDB.Compass",
}


def register_installer_tools(registry) -> None:
    events = EventBus.get()

    def install_app(app_name: str) -> str:
        key = app_name.lower().strip()
        winget_id = WINGET_APPS.get(key)
        events.emit("install_start", {"app": app_name})

        if winget_id:
            events.emit("terminal_output", {"text": f"Installing {app_name} via winget..."})
            output = registry.run_powershell(
                f'winget install --id {winget_id} -e --accept-package-agreements --accept-source-agreements',
                timeout=600,
            )
            events.emit("install_done", {"app": app_name, "method": "winget"})
            return f"Installed {app_name} via winget:\n{output[:3000]}"

        events.emit("terminal_output", {"text": f"Searching winget for {app_name}..."})
        search = registry.run_powershell(f'winget search "{app_name}"', timeout=60)
        if "No package found" in search or not search.strip():
            url = f"https://www.google.com/search?q={app_name.replace(' ', '+')}+download+free"
            registry.run_powershell(f'Start-Process "{url}"')
            events.emit("install_done", {"app": app_name, "method": "browser"})
            return f"No winget match. Opened browser to search for {app_name}. Tell me when downloaded."

        output = registry.run_powershell(
            f'winget install --name "{app_name}" --accept-package-agreements --accept-source-agreements',
            timeout=600,
        )
        events.emit("install_done", {"app": app_name, "method": "winget_search"})
        return f"Install attempt for {app_name}:\n{output[:3000]}"

    registry.register("install_app", "Download and install a free app (GitHub Desktop, VS Code, etc.)",
                      {"app_name": "str"}, install_app)
