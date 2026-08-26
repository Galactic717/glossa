"""The answering model.

Glossa speaks three wire formats and picks one from the base URL unless you
name it explicitly with `GLOSSA_PROVIDER`:

  ollama     http://localhost:11434            local, the default
  openai     any OpenAI-compatible `/v1` URL   OpenRouter, OpenAI, Groq,
                                               llama.cpp, LM Studio, vLLM
  anthropic  https://api.anthropic.com         Claude

Retrieval and embeddings always stay local. Only the final question, the
retrieved fragments and the answer travel to a remote provider -- and nothing
at all travels if you keep the local default.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from .config import Settings

ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_base_url.rstrip("/")
        self.provider = settings.resolved_provider

    # ------------------------------------------------------------------ request

    def _build(
        self, messages: list[dict], model: str | None, stream: bool
    ) -> tuple[str, dict, dict]:
        """Return (url, headers, payload) for the active provider."""
        model = model or self.settings.llm_model
        key = self.settings.llm_api_key

        if self.provider == "anthropic":
            system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
            turns = [m for m in messages if m["role"] != "system"]
            return (
                f"{self.base_url}/v1/messages",
                {
                    "content-type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                },
                {
                    "model": model,
                    "system": system,
                    "messages": turns,
                    "max_tokens": self.settings.max_tokens,
                    "temperature": self.settings.temperature,
                    "stream": stream,
                },
            )

        if self.provider == "openai":
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            return (
                f"{self.base_url}/chat/completions",
                headers,
                {
                    "model": model,
                    "messages": messages,
                    "stream": stream,
                    "temperature": self.settings.temperature,
                    "max_tokens": self.settings.max_tokens,
                },
            )

        return (
            f"{self.base_url}/api/chat",
            {"Content-Type": "application/json"},
            {
                "model": model,
                "messages": messages,
                "stream": stream,
                # Reasoning models (ornith, qwen3, deepseek-r1, gpt-oss)
                # otherwise spend the whole token budget in `message.thinking`
                # and return an empty `content`. Dropped automatically below
                # for models that do not understand the flag.
                "think": False,
                "options": {
                    "temperature": self.settings.temperature,
                    "num_predict": self.settings.max_tokens,
                },
            },
        )

    @staticmethod
    def _thinking_rejected(payload: dict, status: int, body: str) -> bool:
        """True when the server refused the `think` flag rather than the request."""
        return status == 400 and "think" in payload and "think" in body.lower()

    # ------------------------------------------------------------------ response

    def _extract(self, chunk: dict) -> str:
        if self.provider == "anthropic":
            if "delta" in chunk:  # streaming event
                return chunk["delta"].get("text", "") or ""
            blocks = chunk.get("content") or []
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if self.provider == "openai":
            choice = (chunk.get("choices") or [{}])[0]
            return (choice.get("delta") or choice.get("message") or {}).get("content", "") or ""
        return (chunk.get("message") or {}).get("content", "") or ""

    # ------------------------------------------------------------------ api

    def chat(self, messages: list[dict], model: str | None = None) -> str:
        url, headers, payload = self._build(messages, model, stream=False)
        try:
            for attempt in (0, 1):
                response = httpx.post(
                    url, json=payload, headers=headers, timeout=self.settings.request_timeout
                )
                if attempt == 0 and self._thinking_rejected(
                    payload, response.status_code, response.text
                ):
                    payload.pop("think")
                    continue
                break
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise LLMError(self._explain(exc.response.status_code, exc.response.text)) from exc
        except httpx.HTTPError as exc:
            raise LLMError(self._unreachable(exc)) from exc
        return self._extract(response.json()).strip()

    def stream(self, messages: list[dict], model: str | None = None) -> Iterator[str]:
        url, headers, payload = self._build(messages, model, stream=True)
        try:
            for attempt in (0, 1):
                with httpx.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.request_timeout,
                ) as response:
                    if attempt == 0 and response.status_code == 400 and "think" in payload:
                        response.read()
                        if self._thinking_rejected(payload, 400, response.text):
                            payload.pop("think")
                            continue
                    if response.status_code >= 400:
                        response.read()
                        raise LLMError(self._explain(response.status_code, response.text))
                    yield from self._parse_stream(response)
                    return
        except httpx.HTTPError as exc:
            raise LLMError(self._unreachable(exc)) from exc

    def _parse_stream(self, response: httpx.Response) -> Iterator[str]:
        for line in response.iter_lines():
            if not line:
                continue
            if line.startswith("event:"):  # anthropic frames name their event
                continue
            if line.startswith("data: "):
                line = line[6:]
                if line.strip() == "[DONE]":
                    return
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            piece = self._extract(chunk)
            if piece:
                yield piece

    # ------------------------------------------------------------------ discovery

    def _models_url(self) -> str:
        if self.provider == "ollama":
            return f"{self.base_url}/api/tags"
        if self.provider == "anthropic":
            return f"{self.base_url}/v1/models"
        return f"{self.base_url}/models"

    def _auth_headers(self) -> dict[str, str]:
        if self.provider == "anthropic":
            return {"x-api-key": self.settings.llm_api_key, "anthropic-version": ANTHROPIC_VERSION}
        if self.settings.llm_api_key:
            return {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        return {}

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(self._models_url(), headers=self._auth_headers(), timeout=15)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError:
            return []
        if self.provider == "ollama":
            return sorted(item["name"] for item in data.get("models", []))
        return sorted(item["id"] for item in data.get("data", []))

    def health(self) -> bool:
        if self.provider != "ollama" and not self.settings.llm_api_key:
            return False
        try:
            status = httpx.get(
                self._models_url(), headers=self._auth_headers(), timeout=10
            ).status_code
        except httpx.HTTPError:
            return False
        return status < 500 and status not in (401, 403)

    # ------------------------------------------------------------------ errors

    def _explain(self, status: int, body: str) -> str:
        hint = ""
        if status in (401, 403):
            hint = (
                f" Set GLOSSA_API_KEY for {self.provider}."
                if not self.settings.llm_api_key
                else " The API key was rejected."
            )
        elif status == 404:
            hint = f" Check that model '{self.settings.llm_model}' exists on this provider."
        elif status == 429:
            hint = " Rate limited by the provider."
        return f"{self.provider} returned {status}:{hint} {body[:300]}"

    def _unreachable(self, exc: Exception) -> str:
        if self.provider == "ollama":
            return (
                f"Cannot reach Ollama at {self.base_url}. "
                f"Start it with `ollama serve`, "
                f"then `ollama pull {self.settings.llm_model}`. ({exc})"
            )
        return f"Cannot reach {self.provider} at {self.base_url}: {exc}"
