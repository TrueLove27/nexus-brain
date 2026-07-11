"""Free local LLM via Ollama — zero cost, runs on your machine."""

from __future__ import annotations

import requests

from .provider import LLMProvider


class OllamaProvider(LLMProvider):
    def __init__(self, model: str, base_url: str = "http://localhost:11434",
                 max_tokens: int = 4096):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = [m["name"].split(":")[0] for m in resp.json().get("models", [])]
            base = self.model.split(":")[0]
            return base in models or self.model in [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            return False

    def chat(self, messages: list[dict], temperature: float = 0.1) -> str:
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": self.max_tokens},
            },
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]
