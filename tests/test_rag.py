"""Unit tests for the pieces that do not need a model download."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import languages, utils
from backend.app.rag import RAGEngine

# --------------------------------------------------------------------------- languages


def test_ukrainian_is_supported_and_detected():
    text = "Це українськомовний документ про історію та їжу, який ми індексуємо."
    assert languages.detect(text) == "uk"
    assert languages.ensure_supported(text) == "uk"


def test_russian_is_rejected():
    text = "Это русскоязычный текст, который мы не поддерживаем в этом проекте."
    with pytest.raises(languages.UnsupportedLanguage) as error:
        languages.ensure_supported(text)
    assert error.value.code == "ru"


def test_russian_absent_from_the_supported_list():
    assert "ru" not in languages.LANGUAGES
    assert len(languages.LANGUAGES) >= 20


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("This is an English sentence about documents and retrieval.", "en"),
        ("To jest polski tekst o dokumentach i wyszukiwaniu informacji.", "pl"),
        ("Dies ist ein deutscher Satz über Dokumente und Informationssuche.", "de"),
    ],
)
def test_detects_other_supported_languages(text, code):
    assert languages.detect(text) == code


def test_cyrillic_tie_break_prefers_exclusive_letters():
    # Short Ukrainian strings are exactly where a statistical detector slips.
    assert languages.detect("Дякую, це чудово! Їжа і ґанок.") == "uk"


# --------------------------------------------------------------------------- chunking


def test_chunks_respect_size_and_carry_provenance():
    blocks = [
        utils.Block(text="Речення один. " * 40, page=1),
        utils.Block(text="Другий блок тексту. " * 40, page=2),
    ]
    chunks = utils.chunk_blocks(blocks, size=200, overlap=40)

    assert len(chunks) > 2
    assert all(len(chunk.text) <= 260 for chunk in chunks)
    assert {chunk.page for chunk in chunks} == {1, 2}
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_chunks_never_span_two_blocks():
    blocks = [utils.Block(text="Alpha.", page=1), utils.Block(text="Beta.", page=2)]
    chunks = utils.chunk_blocks(blocks, size=1000, overlap=100)
    assert [(chunk.text, chunk.page) for chunk in chunks] == [("Alpha.", 1), ("Beta.", 2)]


def test_oversized_sentence_is_hard_wrapped():
    chunks = utils.chunk_blocks([utils.Block(text="x" * 5000)], size=500, overlap=50)
    assert chunks
    assert all(len(chunk.text) <= 600 for chunk in chunks)


# --------------------------------------------------------------------------- parsing


def test_parses_txt_and_markdown(tmp_path: Path):
    plain = tmp_path / "note.txt"
    plain.write_text("Просто текст.", encoding="utf-8")
    assert utils.parse_file(plain)[0].text == "Просто текст."

    markdown = tmp_path / "doc.md"
    markdown.write_text("# Заголовок\n\nТіло тексту.\n\n## Друга\n\nЩе текст.", encoding="utf-8")
    blocks = utils.parse_file(markdown)
    assert [block.section for block in blocks] == ["Заголовок", "Друга"]


def test_rejects_unknown_extension(tmp_path: Path):
    weird = tmp_path / "data.xyz"
    weird.write_text("hello", encoding="utf-8")
    with pytest.raises(utils.UnsupportedFormat):
        utils.parse_file(weird)


def test_file_id_is_content_addressed(tmp_path: Path):
    first = tmp_path / "a.txt"
    second = tmp_path / "b.txt"
    first.write_text("same", encoding="utf-8")
    second.write_text("same", encoding="utf-8")
    assert utils.file_id(first) == utils.file_id(second)


# --------------------------------------------------------------------------- retrieval maths


def test_mmr_drops_a_near_duplicate_for_a_diverse_chunk():
    query = [1.0, 0.0]
    candidates = [
        [0.98, 0.20],  # best match
        [0.97, 0.24],  # nearly identical to the best match
        [0.75, -0.66],  # slightly less relevant, but points elsewhere
    ]
    picked = RAGEngine._mmr(query, candidates, k=2, diversity=0.5)
    assert picked[0] == 0
    assert picked[1] == 2


# --------------------------------------------------------------------------- export


def test_export_round_trip():
    answer = {
        "question": "Скільки?",
        "answer": "Сорок два [1].",
        "sources": [
            {
                "ref": 1,
                "doc_id": "abc",
                "filename": "book.pdf",
                "location": "p. 7",
                "page": 7,
                "section": None,
                "score": 0.91,
                "text": "Відповідь: 42",
            }
        ],
        "meta": {"model": "ornith:9b", "profile": "fast"},
    }

    markdown = utils.answer_to_markdown(answer)
    assert "Сорок два [1]." in markdown
    assert "book.pdf" in markdown
    assert "> Відповідь: 42" in markdown

    import json

    assert json.loads(utils.answer_to_json(answer))["sources"][0]["page"] == 7


# --------------------------------------------------------------------------- ingestion order


def test_embedder_loads_before_any_parser_runs(tmp_path: Path, monkeypatch):
    """Regression guard for a hard crash, not a style preference.

    pypdf imports cryptography, whose native OpenSSL module segfaults the
    process on Windows when torch is loaded afterwards. add_document must warm
    the embedding model before it touches a parser.
    """
    from backend.app import rag
    from backend.app.config import Settings

    order: list[str] = []

    monkeypatch.setattr(
        rag.RAGEngine,
        "embedder",
        property(lambda self: order.append("embedder")),
    )

    def fake_parse(path):
        order.append("parse")
        raise utils.UnsupportedFormat("stop here")

    monkeypatch.setattr(rag.utils, "parse_file", fake_parse)

    document = tmp_path / "doc.pdf"
    document.write_bytes(b"%PDF-1.4 fake")

    engine = rag.RAGEngine(Settings(data_dir=tmp_path / "data"))
    with pytest.raises(utils.UnsupportedFormat):
        engine.add_document(document)

    assert order == ["embedder", "parse"]
