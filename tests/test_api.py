"""API tests. The engine is stubbed, so no model is downloaded."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import languages, main


@pytest.fixture
def client(monkeypatch):
    documents = [
        {
            "id": "doc1",
            "filename": "handbook.pdf",
            "size_bytes": 2048,
            "chunks": 12,
            "language": "uk",
            "language_name": "Українська",
            "pages": 4,
            "added_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    sources = [
        {
            "ref": 1,
            "doc_id": "doc1",
            "filename": "handbook.pdf",
            "location": "p. 2",
            "page": 2,
            "section": None,
            "score": 0.87,
            "text": "Гарантія діє 24 місяці.",
        }
    ]

    monkeypatch.setattr(main.engine, "list_documents", lambda: documents)
    monkeypatch.setattr(main.engine, "stats", lambda: {"documents": 1, "chunks": 12})
    monkeypatch.setattr(main.engine.llm, "health", lambda: True)
    monkeypatch.setattr(main.engine.llm, "list_models", lambda: ["ornith:9b"])
    monkeypatch.setattr(
        main.engine, "search", lambda *args, **kwargs: sources
    )
    monkeypatch.setattr(
        main.engine,
        "ask",
        lambda question, **kwargs: {
            "question": question,
            "answer": "24 місяці [1].",
            "sources": sources,
            "meta": {
                "model": "ornith:9b",
                "profile": "balanced",
                "language": languages.detect(question),
                "chunks_used": 1,
                "elapsed_ms": 42,
            },
        },
    )
    return TestClient(main.app)


def test_health_reports_library_and_languages(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["documents"] == 1 and body["chunks"] == 12
    assert "ru" not in body["languages"]
    assert {profile["name"] for profile in body["profiles"]} == {"fast", "balanced", "quality"}


def test_lists_documents(client):
    body = client.get("/api/documents").json()
    assert body[0]["filename"] == "handbook.pdf"
    assert body[0]["language_name"] == "Українська"


def test_ask_returns_answer_with_citations(client):
    response = client.post("/api/ask", json={"question": "Яка гарантія?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "24 місяці [1]."
    assert body["sources"][0]["page"] == 2
    assert body["meta"]["language"] == "uk"


def test_ask_rejects_russian(client, monkeypatch):
    def blow_up(question, **kwargs):
        raise languages.UnsupportedLanguage("ru", "Русский")

    monkeypatch.setattr(main.engine, "ask", blow_up)
    response = client.post("/api/ask", json={"question": "Какая гарантия на товар?"})
    assert response.status_code == 422
    assert "not supported" in response.json()["detail"]


def test_search_endpoint_skips_the_llm(client):
    body = client.post("/api/search", json={"query": "гарантія", "top_k": 3}).json()
    assert body[0]["ref"] == 1


def test_upload_rejects_unsupported_format(client):
    response = client.post(
        "/api/documents", files={"files": ("virus.exe", b"MZ", "application/octet-stream")}
    )
    assert response.status_code == 415


def test_export_markdown_and_json(client):
    answer = client.post("/api/ask", json={"question": "Яка гарантія?"}).json()

    markdown = client.post("/api/export?format=markdown", json=answer)
    assert markdown.status_code == 200
    assert "handbook.pdf" in markdown.text
    assert "attachment" in markdown.headers["content-disposition"]

    exported = client.post("/api/export?format=json", json=answer)
    assert exported.status_code == 200
    assert json.loads(exported.text)["sources"][0]["filename"] == "handbook.pdf"


def test_stream_emits_sources_then_tokens_then_done(client, monkeypatch):
    def fake_stream(question, **kwargs):
        yield {"type": "sources", "sources": []}
        yield {"type": "token", "text": "24 "}
        yield {"type": "token", "text": "місяці"}
        yield {"type": "done", "meta": {"model": "m", "profile": "fast"}}

    monkeypatch.setattr(main.engine, "ask_stream", fake_stream)
    with client.stream("POST", "/api/ask/stream", json={"question": "Яка гарантія?"}) as response:
        frames = [line for line in response.iter_lines() if line.startswith("data: ")]

    assert len(frames) == 4
    assert '"type": "sources"' in frames[0]
    assert "місяці" in frames[2]
    assert '"type": "done"' in frames[3]


# --------------------------------------------------------------------------- providers


def _client(**kwargs):
    from backend.app.config import Settings
    from backend.app.llm import LLMClient

    return LLMClient(Settings(**kwargs))


MESSAGES = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
]


def test_provider_is_detected_from_the_base_url():
    from backend.app.config import detect_provider

    assert detect_provider("http://localhost:11434") == "ollama"
    assert detect_provider("https://openrouter.ai/api/v1") == "openai"
    assert detect_provider("https://api.openai.com/v1") == "openai"
    assert detect_provider("http://localhost:8080/v1") == "openai"
    assert detect_provider("https://api.anthropic.com") == "anthropic"


def test_explicit_provider_overrides_detection():
    client = _client(llm_base_url="https://my-proxy.example.com", provider="anthropic")
    assert client.provider == "anthropic"


def test_ollama_request_disables_thinking():
    client = _client(llm_base_url="http://localhost:11434")
    url, headers, payload = client._build(MESSAGES, None, stream=False)

    assert url.endswith("/api/chat")
    # Reasoning models such as the default ornith:9b otherwise burn the whole
    # budget in `thinking` and return an empty `content`.
    assert payload["think"] is False
    assert payload["messages"] == MESSAGES
    assert "Authorization" not in headers
    assert client._thinking_rejected(payload, 400, "does not support think")
    assert not client._thinking_rejected(payload, 400, "model not found")


def test_openai_compatible_request_carries_the_bearer_token():
    client = _client(llm_base_url="https://openrouter.ai/api/v1", llm_api_key="sk-or-test")
    url, headers, payload = client._build(MESSAGES, "anthropic/claude-sonnet-5", stream=True)

    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-or-test"
    assert payload["model"] == "anthropic/claude-sonnet-5"
    assert payload["stream"] is True
    assert "think" not in payload and "options" not in payload


def test_anthropic_request_lifts_the_system_prompt_out_of_messages():
    client = _client(llm_base_url="https://api.anthropic.com", llm_api_key="sk-ant-test")
    url, headers, payload = client._build(MESSAGES, "claude-sonnet-5", stream=False)

    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"]
    # Anthropic rejects a `system` role inside `messages`.
    assert payload["system"] == "be brief"
    assert [m["role"] for m in payload["messages"]] == ["user"]
    assert payload["max_tokens"] > 0


@pytest.mark.parametrize(
    ("base_url", "chunk", "expected"),
    [
        ("http://localhost:11434", {"message": {"content": "a"}}, "a"),
        ("https://api.openai.com/v1", {"choices": [{"delta": {"content": "b"}}]}, "b"),
        ("https://api.openai.com/v1", {"choices": [{"message": {"content": "c"}}]}, "c"),
        ("https://api.anthropic.com", {"content": [{"type": "text", "text": "d"}]}, "d"),
        ("https://api.anthropic.com", {"delta": {"type": "text_delta", "text": "e"}}, "e"),
    ],
)
def test_every_provider_response_shape_yields_text(base_url, chunk, expected):
    assert _client(llm_base_url=base_url)._extract(chunk) == expected


def test_remote_provider_without_a_key_reports_unhealthy():
    # Better a clear "offline" badge than a 401 on the first question.
    assert _client(llm_base_url="https://api.openai.com/v1").health() is False


def test_missing_key_error_names_the_variable_to_set():
    client = _client(llm_base_url="https://api.openai.com/v1")
    assert "GLOSSA_API_KEY" in client._explain(401, "unauthorized")
