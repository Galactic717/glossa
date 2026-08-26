# Contributing

Thanks for helping out. The project stays small on purpose, so the bar for new
dependencies is high and the bar for new files is higher.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cd frontend && npm install
```

## Before opening a pull request

```bash
ruff check backend cli tests
pytest -q
cd frontend && npm run typecheck
```

## Ground rules

- **No Russian language support.** This is a product decision, not an oversight.
  Pull requests adding `ru` to `backend/app/languages.py` will be closed.
- **Documents never leave the machine.** Parsing, chunking, embedding and
  retrieval are local, always. A hosted provider may answer the question, but
  it only ever sees the question, the retrieved fragments and its own reply.
  The default configuration must keep working with the network unplugged.
- **New providers must not add dependencies.** The three wire formats in
  `backend/app/llm.py` are plain `httpx` calls. A provider SDK is not a reason
  to add one.
- **Citations are mandatory.** Any change to the answer path must keep the
  source references intact.
- Adding a dependency? Say in the PR what it replaces and why a few lines of
  standard library would not do.

## Adding a language

Add the ISO 639-1 code and native name to `LANGUAGES` in
`backend/app/languages.py`, then check that `langdetect` recognises it. If it
does not, the language needs a detection path before it can be added.
