"""Provider-Abstraktion für die KI-Anbindung.

Ein einheitliches Interface (`AIProvider.complete`) über mehrere Backends:

* ``anthropic`` – Claude über das offizielle SDK (Default)
* ``ollama``    – lokales Modell (Datenschutz, keine Cloud)
* ``openai``    – optional
* ``disabled``  – KI aus (Fallback ohne Vorschläge)

Der konkrete Provider wird über die Settings/ENV gewählt (``AI_PROVIDER``).
Höhere Schichten (services.py) kennen nur dieses Interface, nicht das Backend.
"""
from __future__ import annotations

from django.conf import settings


class AIProvider:
    """Basis-Interface. Implementierungen liefern Text zu einem Prompt."""

    name = "base"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise NotImplementedError

    @property
    def available(self) -> bool:
        return True


class DisabledProvider(AIProvider):
    name = "disabled"

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise RuntimeError("KI ist deaktiviert (AI_PROVIDER=disabled).")

    @property
    def available(self) -> bool:
        return False


class AnthropicProvider(AIProvider):
    """Claude über das Anthropic Python SDK."""

    name = "anthropic"

    def __init__(self) -> None:
        self.model = settings.AI_MODEL
        self.api_key = settings.ANTHROPIC_API_KEY

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        kwargs: dict = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        # Adaptives Denken für anspruchsvollere Extraktion/Klassifizierung.
        # (Auf älteren Modellen ignoriert das SDK dies bzw. es ist der Default.)
        if self.model.startswith(("claude-opus-4", "claude-sonnet-5", "claude-fable")):
            kwargs["thinking"] = {"type": "adaptive"}

        response = client.messages.create(**kwargs)
        return "".join(
            block.text for block in response.content if block.type == "text"
        )


class OllamaProvider(AIProvider):
    """Lokales Modell über die Ollama-HTTP-API (keine Cloud, Datenschutz)."""

    name = "ollama"

    def __init__(self) -> None:
        self.model = settings.AI_MODEL
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import httpx

        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        resp = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        return resp.json().get("response", "")


class OpenAIProvider(AIProvider):
    """OpenAI-kompatibles Chat-Completions-Endpoint (optional)."""

    name = "openai"

    def __init__(self) -> None:
        self.model = settings.AI_MODEL
        self.api_key = settings.OPENAI_API_KEY

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, prompt: str, *, system: str | None = None) -> str:
        import httpx

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "messages": messages},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


_PROVIDERS = {
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "openai": OpenAIProvider,
    "disabled": DisabledProvider,
}


def get_provider() -> AIProvider:
    """Liefert die in den Settings konfigurierte Provider-Instanz."""
    provider_cls = _PROVIDERS.get(settings.AI_PROVIDER, DisabledProvider)
    return provider_cls()
