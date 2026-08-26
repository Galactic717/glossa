# Usage

## Start it

```bash
glossa
```

One command. It builds the web UI if needed, starts the API and opens
<http://localhost:8000>. Add `--no-open` to skip the browser, `--port 9000` to
move it.

## The CLI

One binary, four ways to drive it.

### 1. Everything

```bash
glossa
```

### 2. One-shot

```bash
glossa add contract.pdf report.docx notes.md
glossa ask "Які терміни оплати?"
```

Point it at a folder and it walks the tree:

```bash
glossa add ./documents
```

### 3. Interactive

```bash
glossa chat
```

Answers stream token by token, with the matched fragments listed above them.
In-chat commands:

| Command | Effect |
|---|---|
| `/docs` | list indexed documents |
| `/profile quality` | switch preset mid-conversation |
| `/sources` | reprint the last sources in full |
| `/save answer.md` | export the last answer (`.md` or `.json`) |
| `/reset` | forget the conversation history |
| `/exit` | quit |

### 4. Scripted

```bash
glossa ask "Summarise the risks" --json | jq -r .answer
glossa ask "Які ризики?" --md --out risks.md
```

### Every command

| Command | What it does |
|---|---|
| `glossa` | Start the API and web UI, open the browser |
| `glossa add PATH...` | Index files or folders |
| `glossa docs` | List the library |
| `glossa rm ID` | Delete one document |
| `glossa clear` | Delete everything |
| `glossa ask "Q"` | One question, one answer |
| `glossa chat` | Interactive REPL |
| `glossa search "Q"` | Retrieval only, no LLM (debug relevance) |
| `glossa dash` | Status dashboard |
| `glossa langs` | List the 35 supported languages |
| `glossa models` | List models on the local LLM server |
| `glossa serve` | Same as bare `glossa`, with explicit flags |

### Useful flags

| Flag | Meaning |
|---|---|
| `--doc ID` | Restrict retrieval to one document (repeatable) |
| `--profile fast\|balanced\|quality` | Speed/quality preset |
| `--model NAME` | Override the LLM for this call |
| `--top-k N` | Number of fragments fed to the model |
| `--json` / `--md` | Machine-readable output |
| `--out FILE` | Write the answer to a file |
| `--api URL` | Drive a running server instead of loading models locally |

`--api` is the difference between two deployment shapes. Without it, the CLI
loads the embedding model into its own process, which is right for a laptop.
With it, one server holds the models in memory and every CLI call is a thin
HTTP request:

```bash
glossa serve                                  # terminal 1, machine A
glossa ask "Що це?" --api http://a.local:8000 # terminal 2, anywhere
```

## The web UI

Open <http://localhost:8000> after `glossa serve`.

- **Drag documents** onto the left panel, or click it to browse.
- **Click documents** to narrow the search to a subset. Nothing selected means
  the whole library.
- **Режим** picks the speed/quality preset, **Модель** picks the LLM, and the
  slider sets how many fragments reach the model.
- **Answers stream** as they are generated. Press the stop button to cancel.
- **Citation chips** like `1` inside an answer open the exact fragment it came
  from, with the file name and page.
- **MD / JSON** under each answer download it.

## Speed and quality presets

| Preset | Fragments | Reranker | Typical use |
|---|---|---|---|
| `fast` | 3 | no | Short factual lookups |
| `balanced` | 5 | no | Default |
| `quality` | 8 | yes | Long documents, subtle questions |

`quality` downloads a cross-encoder reranker (about 470 MB) the first time it
runs. If the download is unavailable, it degrades to vector order rather than
failing, and logs a warning.

## Choosing a model

`glossa dash` shows the active provider, the model, and whether it runs
locally. Switching provider is three lines in `.env`:

```bash
GLOSSA_BASE_URL=https://openrouter.ai/api/v1
GLOSSA_MODEL=anthropic/claude-sonnet-5
GLOSSA_API_KEY=sk-or-...
```

`glossa models` lists what the current endpoint offers, and `--model NAME`
overrides it for a single call without touching `.env`.

Whatever you choose, parsing, chunking, embedding and retrieval stay on your
machine. Only the question, the retrieved fragments and the answer are sent to
a hosted provider.

## Reasoning models

The default `ornith:9b`, plus qwen3, deepseek-r1, gpt-oss and friends, split
their output into a `thinking`
field and a `content` field. Left alone they exhaust the token budget thinking
and return an empty answer, so the project asks Ollama for `think: false` and
retries without the flag if the server rejects it. Nothing to configure.

Note that reasoning models are also the ones most likely to answer in English
regardless of the question. Every request names the target language explicitly,
but a small English-centric model can still ignore it. If answers drift to
English, that is the model, not the retrieval — try `gemma3`, `qwen3` or
`mistral-small` instead.

## Languages

35 languages are supported. Russian is deliberately not one of them: documents
and questions detected as Russian are rejected with an explicit message. See
`glossa langs` for the full list.

Ukrainian and Russian are separated by script-exclusive letters
(`і ї є ґ` against `ы э ъ ё`) before the statistical detector runs, so short
Ukrainian phrases are never misfiled.

Answers come back in the language of the question, whatever language the
document is written in. Asking an English question about a Ukrainian PDF gives
an English answer citing the Ukrainian source.

## Multiple documents

Everything lives in one collection, so questions span the whole library by
default. Narrow it with `--doc` on the CLI or by selecting documents in the UI.
Re-adding the same file replaces it instead of duplicating it, because document
ids are content hashes.

## Export format

```json
{
  "question": "Які терміни оплати?",
  "answer": "Оплата протягом 30 днів [1].",
  "sources": [
    {
      "ref": 1,
      "doc_id": "9f2c4a1b8e7d6c5f",
      "filename": "contract.pdf",
      "location": "p. 4",
      "page": 4,
      "section": null,
      "score": 0.8123,
      "text": "Замовник сплачує протягом 30 календарних днів..."
    }
  ],
  "meta": {
    "model": "ornith:9b",
    "profile": "balanced",
    "language": "uk",
    "chunks_used": 5,
    "elapsed_ms": 4210
  }
}
```
