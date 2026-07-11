from __future__ import annotations

from pathlib import Path

from .registry import ToolRegistry


def register_code_tools(registry: ToolRegistry) -> None:
    ws = registry.workspace

    def search_code(query: str, directory: str = ".", file_pattern: str = "*.*",
                    max_results: int = 25) -> str:
        p = Path(directory) if Path(directory).is_absolute() else ws / directory
        matches = []
        for f in p.rglob(file_pattern):
            if not f.is_file() or f.suffix in {".exe", ".dll", ".png", ".jpg", ".zip"}:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
                if query.lower() in text.lower():
                    for i, line in enumerate(text.splitlines(), 1):
                        if query.lower() in line.lower():
                            rel = f.relative_to(ws) if f.is_relative_to(ws) else f
                            matches.append(f"{rel}:{i}: {line.strip()[:120]}")
                            break
            except Exception:
                continue
            if len(matches) >= max_results:
                break
        return "\n".join(matches) if matches else f"No matches for '{query}'"

    def create_project(name: str, project_type: str = "python", path: str = "") -> str:
        base = Path(path) if path else ws / name
        base.mkdir(parents=True, exist_ok=True)
        if project_type == "python":
            (base / "main.py").write_text('def main():\n    print("Hello")\n\nif __name__ == "__main__":\n    main()\n')
            (base / "requirements.txt").write_text("")
            return f"Created Python project at {base}"
        if project_type == "node":
            (base / "package.json").write_text(f'{{"name": "{name}", "version": "1.0.0", "main": "index.js"}}')
            (base / "index.js").write_text('console.log("Hello");\n')
            return f"Created Node project at {base}"
        return f"Created directory at {base}"

    registry.register("search_code", "Search for text in source files",
                      {"query": "str", "directory": "str (optional)", "file_pattern": "str (optional)"},
                      search_code)
    registry.register("create_project", "Scaffold a new code project",
                      {"name": "str", "project_type": "python|node", "path": "str (optional)"},
                      create_project)
