"""Glossa -- command line interface.

`glossa` with no arguments starts everything: it builds the web UI if needed,
serves the API, and opens the browser. That is the whole launch procedure.

Four ways to drive it from this one entry point:

  1. everything    glossa                       (server + web UI + browser)
  2. one-shot      glossa ask "your question"
  3. interactive   glossa chat                  (REPL, streaming, live sources)
  4. scripted      glossa ask "..." --json | jq .answer

Add `--api http://host:8000` to any command to drive a running server instead
of loading the models into this process.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import httpx
import typer
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Windows consoles still default to a legacy code page, which turns every
# Ukrainian character into a question mark. Force UTF-8 before Rich starts.
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

from backend.app import languages, utils  # noqa: E402
from backend.app.config import PROFILES, settings  # noqa: E402

console = Console()
ROOT = Path(__file__).resolve().parents[1]

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    rich_markup_mode="rich",
    help="Ask your documents. 35 languages, cited answers, your choice of model.",
)

API_OPTION = typer.Option(None, "--api", help="Drive a running server instead of local models.")


class _Quiet:
    """A do-nothing context manager, so --json output stays pipe-clean."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


# --------------------------------------------------------------------------- backends


class LocalBackend:
    """Runs the engine in-process. No server needed."""

    label = "local"

    def __init__(self) -> None:
        from backend.app.rag import RAGEngine

        self.engine = RAGEngine(settings)

    def add(self, path: Path) -> dict:
        return self.engine.add_document(path)

    def documents(self) -> list[dict]:
        return self.engine.list_documents()

    def delete(self, doc_id: str) -> None:
        self.engine.delete_document(doc_id)

    def clear(self) -> None:
        self.engine.clear()

    def ask(self, question: str, **kwargs) -> dict:
        return self.engine.ask(question, **kwargs)

    def stream(self, question: str, **kwargs):
        return self.engine.ask_stream(question, **kwargs)

    def search(self, query: str, top_k: int) -> list[dict]:
        return self.engine.search(query, top_k=top_k)

    def health(self) -> dict:
        stats = self.engine.stats()
        return {
            "llm_reachable": self.engine.llm.health(),
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            **stats,
        }

    def models(self) -> list[str]:
        return self.engine.llm.list_models()


class ApiBackend:
    """Talks HTTP to a running `glossa serve`."""

    label = "api"

    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=300)

    def _json(self, method: str, path: str, **kwargs):
        response = self.client.request(method, f"{self.base}{path}", **kwargs)
        if response.status_code >= 400:
            detail = response.json().get("detail", response.text)
            raise typer.BadParameter(str(detail))
        return response.json()

    def add(self, path: Path) -> dict:
        with path.open("rb") as handle:
            return self._json("POST", "/api/documents", files={"files": (path.name, handle)})[0]

    def documents(self) -> list[dict]:
        return self._json("GET", "/api/documents")

    def delete(self, doc_id: str) -> None:
        self._json("DELETE", f"/api/documents/{doc_id}")

    def clear(self) -> None:
        self._json("DELETE", "/api/documents")

    def ask(self, question: str, **kwargs) -> dict:
        return self._json("POST", "/api/ask", json=_ask_payload(question, kwargs))

    def stream(self, question: str, **kwargs):
        with self.client.stream(
            "POST", f"{self.base}/api/ask/stream", json=_ask_payload(question, kwargs)
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    yield json.loads(line[6:])

    def search(self, query: str, top_k: int) -> list[dict]:
        return self._json("POST", "/api/search", json={"query": query, "top_k": top_k})

    def health(self) -> dict:
        return self._json("GET", "/api/health")

    def models(self) -> list[str]:
        return self._json("GET", "/api/models")["available"]


def _ask_payload(question: str, kwargs: dict) -> dict:
    return {
        "question": question,
        "doc_ids": kwargs.get("doc_ids"),
        "profile": kwargs.get("profile_name"),
        "model": kwargs.get("model"),
        "top_k": kwargs.get("top_k"),
        "history": kwargs.get("history") or [],
    }


def backend_for(api: str | None):
    if api:
        return ApiBackend(api)
    with console.status("[dim]loading models...", spinner="dots"):
        return LocalBackend()


# --------------------------------------------------------------------------- rendering


def render_sources(sources: list[dict], full: bool = False) -> Panel:
    body = []
    for source in sources:
        where = f" · {source['location']}" if source.get("location") else ""
        header = Text()
        header.append(f"[{source['ref']}] ", style="bold cyan")
        header.append(source["filename"], style="bold")
        header.append(f"{where}  ", style="dim")
        header.append(f"{source['score']:.3f}", style="green")
        snippet = source["text"] if full else source["text"][:220].replace("\n", " ") + "..."
        body.append(header)
        body.append(Text(snippet, style="dim"))
        body.append(Text(""))
    return Panel(Group(*body[:-1]) if body else Text("no sources"), title="Sources", border_style="cyan")


def render_answer(result: dict) -> None:
    console.print(Panel(Markdown(result["answer"]), title="Answer", border_style="green"))
    if result.get("sources"):
        console.print(render_sources(result["sources"]))
    meta = result.get("meta", {})
    if meta:
        console.print(
            Text(
                "  ".join(f"{key}={value}" for key, value in meta.items()),
                style="dim italic",
            )
        )


# --------------------------------------------------------------------------- commands


@app.command()
def add(
    paths: list[Path] = typer.Argument(..., help="Files or directories to index."),
    api: str = API_OPTION,
) -> None:
    """Index PDF / DOCX / TXT / MD documents."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(
                child
                for child in sorted(path.rglob("*"))
                if child.suffix.lower() in utils.SUPPORTED_SUFFIXES
            )
        else:
            files.append(path)

    if not files:
        console.print("[yellow]No supported files found.[/]")
        raise typer.Exit(1)

    client = backend_for(api)
    failed = 0
    with Progress(
        SpinnerColumn(), TextColumn("{task.description}"), console=console, transient=True
    ) as progress:
        task = progress.add_task("indexing", total=None)
        for file in files:
            progress.update(task, description=f"indexing {file.name}")
            try:
                record = client.add(file)
            except Exception as exc:  # one bad file must not abort the batch
                failed += 1
                console.print(f"[red]x[/] {file.name}: {exc}")
                continue
            console.print(
                f"[green]+[/] {record['filename']}  "
                f"[dim]{record['chunks']} chunks · {record['language_name']}[/]"
            )
    if failed:
        raise typer.Exit(1)


@app.command(name="docs")
def list_docs(api: str = API_OPTION) -> None:
    """List indexed documents."""
    documents = backend_for(api).documents()
    if not documents:
        console.print("[yellow]Library is empty. Add something with[/] [bold]glossa add file.pdf[/]")
        return

    table = Table(title=f"{len(documents)} document(s)", header_style="bold cyan")
    table.add_column("id", style="dim")
    table.add_column("file")
    table.add_column("lang")
    table.add_column("chunks", justify="right")
    table.add_column("size", justify="right")
    table.add_column("added", style="dim")
    for document in documents:
        table.add_row(
            document["id"],
            document["filename"],
            document["language_name"],
            str(document["chunks"]),
            f"{document['size_bytes'] / 1024:.0f} KB",
            document["added_at"][:16].replace("T", " "),
        )
    console.print(table)


@app.command(name="rm")
def remove(doc_id: str, api: str = API_OPTION) -> None:
    """Delete one document by id."""
    backend_for(api).delete(doc_id)
    console.print(f"[green]deleted[/] {doc_id}")


@app.command()
def clear(
    api: str = API_OPTION,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete every indexed document."""
    if not yes and not typer.confirm("Delete the entire library?"):
        raise typer.Abort()
    backend_for(api).clear()
    console.print("[green]library cleared[/]")


@app.command()
def ask(
    question: str,
    docs: list[str] = typer.Option(None, "--doc", "-d", help="Restrict to these document ids."),
    profile: str = typer.Option(None, "--profile", "-p", help="fast | balanced | quality"),
    model: str = typer.Option(None, "--model", "-m"),
    top_k: int = typer.Option(None, "--top-k", "-k"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
    as_markdown: bool = typer.Option(False, "--md", help="Markdown output."),
    output: Path = typer.Option(None, "--out", "-o", help="Write the result to a file."),
    api: str = API_OPTION,
) -> None:
    """Ask one question and print the answer with citations."""
    client = backend_for(api)
    try:
        spinner = _Quiet() if as_json else console.status("[dim]thinking...", spinner="dots")
        with spinner:
            result = client.ask(
                question,
                doc_ids=list(docs) if docs else None,
                profile_name=profile,
                model=model,
                top_k=top_k,
            )
    except languages.UnsupportedLanguage as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(2) from exc

    if as_json:
        text = utils.answer_to_json(result)
    elif as_markdown:
        text = utils.answer_to_markdown(result)
    else:
        text = None

    if output:
        output.write_text(text or utils.answer_to_markdown(result), encoding="utf-8")
        console.print(f"[green]written[/] {output}")
        return
    if text:
        print(text)
    else:
        render_answer(result)


@app.command()
def chat(
    docs: list[str] = typer.Option(None, "--doc", "-d"),
    profile: str = typer.Option(None, "--profile", "-p"),
    model: str = typer.Option(None, "--model", "-m"),
    api: str = API_OPTION,
) -> None:
    """Interactive REPL with streaming answers and live source panels."""
    client = backend_for(api)
    history: list[dict] = []

    console.print(
        Panel(
            Text.from_markup(
                "[bold]Glossa[/]\n"
                "[dim]/docs  list documents   /profile <name>  switch preset\n"
                "/sources  show last sources in full   /save <file>  export last answer\n"
                "/reset  clear conversation   /exit  quit[/]"
            ),
            border_style="cyan",
        )
    )

    last: dict | None = None
    while True:
        try:
            question = console.input("[bold cyan]>[/] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not question:
            continue

        if question.startswith("/"):
            command, _, argument = question[1:].partition(" ")
            if command in {"exit", "quit", "q"}:
                return
            if command == "reset":
                history.clear()
                console.print("[dim]conversation cleared[/]")
            elif command == "docs":
                list_docs(api=api)
            elif command == "profile":
                if argument in PROFILES:
                    profile = argument
                    console.print(f"[dim]profile -> {profile}[/]")
                else:
                    console.print(f"[yellow]profiles: {', '.join(PROFILES)}[/]")
            elif command == "sources" and last:
                console.print(render_sources(last["sources"], full=True))
            elif command == "save" and last:
                target = Path(argument or "answer.md")
                body = (
                    utils.answer_to_json(last)
                    if target.suffix == ".json"
                    else utils.answer_to_markdown(last)
                )
                target.write_text(body, encoding="utf-8")
                console.print(f"[green]saved[/] {target}")
            else:
                console.print("[yellow]unknown command[/]")
            continue

        sources: list[dict] = []
        answer_parts: list[str] = []
        meta: dict = {}
        console.print(Rule(style="dim"))
        try:
            for event in client.stream(
                question,
                doc_ids=list(docs) if docs else None,
                profile_name=profile,
                model=model,
                history=history,
            ):
                if event["type"] == "sources":
                    sources = event["sources"]
                    refs = ", ".join(
                        f"[{s['ref']}] {s['filename']}"
                        + (f" {s['location']}" if s["location"] else "")
                        for s in sources
                    )
                    console.print(Text(refs or "no matching fragments", style="dim cyan"))
                    console.print()
                elif event["type"] == "token":
                    answer_parts.append(event["text"])
                    console.print(event["text"], end="", highlight=False)
                elif event["type"] == "done":
                    meta = event["meta"]
                elif event["type"] == "error":
                    console.print(f"[red]{event['detail']}[/]")
        except Exception as exc:
            console.print(f"[red]{exc}[/]")
            continue

        console.print("\n")
        if meta:
            console.print(
                Text("  ".join(f"{k}={v}" for k, v in meta.items()), style="dim italic")
            )
        answer = "".join(answer_parts)
        last = {"question": question, "answer": answer, "sources": sources, "meta": meta}
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": answer})


@app.command()
def search(
    query: str,
    top_k: int = typer.Option(5, "--top-k", "-k"),
    api: str = API_OPTION,
) -> None:
    """Retrieval only, without the LLM. The fastest way to debug relevance."""
    hits = backend_for(api).search(query, top_k)
    console.print(render_sources(hits, full=True))


@app.command()
def dash(api: str = API_OPTION) -> None:
    """Status dashboard: library, models, active profile."""
    client = backend_for(api)
    info = client.health()
    documents = client.documents()

    status = Table.grid(padding=(0, 2))
    status.add_column(style="dim")
    status.add_column()
    reachable = info.get("llm_reachable")
    status.add_row("LLM", f"[{'green' if reachable else 'red'}]{info.get('llm_model')}[/] "
                          f"[dim]{info.get('llm_base_url')}[/] "
                          f"{'online' if reachable else 'offline'}")
    where = "local" if info.get("llm_local", settings.is_local) else "remote"
    status.add_row("Provider", f"{info.get('provider', settings.resolved_provider)} [dim]({where})[/]")
    status.add_row("Embeddings", f"{info.get('embedding_model')} [dim](always local)[/]")
    status.add_row("Profile", f"{settings.profile} [dim]{PROFILES[settings.profile].description}[/]")
    status.add_row("Library", f"{info.get('documents', 0)} documents · {info.get('chunks', 0)} chunks")
    status.add_row("Languages", f"{len(languages.LANGUAGES)} supported")

    by_language: dict[str, int] = {}
    for document in documents:
        by_language[document["language_name"]] = by_language.get(document["language_name"], 0) + 1
    if by_language:
        status.add_row(
            "Breakdown",
            " · ".join(f"{name} {count}" for name, count in sorted(by_language.items())),
        )

    console.print(Panel(status, title="Glossa", border_style="cyan"))


@app.command(name="langs")
def show_languages() -> None:
    """List every supported language."""
    table = Table(header_style="bold cyan", title=f"{len(languages.LANGUAGES)} supported languages")
    table.add_column("code", style="dim")
    table.add_column("language")
    for code, name in languages.LANGUAGES.items():
        table.add_row(code, name)
    console.print(table)


@app.command(name="models")
def show_models(api: str = API_OPTION) -> None:
    """List LLM models available on the local server."""
    available = backend_for(api).models()
    if not available:
        console.print(f"[yellow]No models found at {settings.llm_base_url}. Is Ollama running?[/]")
        return
    for name in available:
        marker = "[green]*[/]" if name == settings.llm_model else " "
        console.print(f"{marker} {name}")


# --------------------------------------------------------------------------- launch


def build_web_ui() -> bool:
    """Build the React app if it has not been built yet. Returns True if usable."""
    frontend = ROOT / "frontend"
    if (frontend / "dist" / "index.html").is_file():
        return True
    if not (frontend / "package.json").is_file():
        return False

    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        console.print(
            "[yellow]Web UI not built and npm is not installed.[/] "
            "Serving the API only -- install Node.js, or use [bold]glossa chat[/]."
        )
        return False

    steps = []
    if not (frontend / "node_modules").is_dir():
        steps.append(("installing web dependencies", [npm, "install", "--no-audit", "--no-fund"]))
    steps.append(("building web UI", [npm, "run", "build"]))

    for description, command in steps:
        with console.status(f"[dim]{description}...", spinner="dots"):
            result = subprocess.run(command, cwd=frontend, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"[yellow]{description} failed -- serving the API only.[/]")
            console.print(f"[dim]{(result.stderr or result.stdout)[-400:]}[/]")
            return False
    return True


def launch(host: str, port: int, reload: bool, open_browser: bool) -> None:
    import uvicorn

    has_ui = build_web_ui()
    shown = "localhost" if host in {"0.0.0.0", "127.0.0.1"} else host
    url = f"http://{shown}:{port}"

    console.print(
        Panel(
            Text.from_markup(
                f"[bold]Glossa[/]\n"
                f"{'Web UI' if has_ui else 'API'}   [cyan]{url}[/]\n"
                f"API docs [cyan]{url}/docs[/]\n"
                f"Model    {settings.llm_model} [dim]via {settings.resolved_provider}[/]"
            ),
            border_style="cyan",
        )
    )

    if open_browser and has_ui:
        # uvicorn.run blocks, so the browser has to be opened from a timer.
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run("backend.app.main:app", host=host, port=port, reload=reload)


@app.command()
def serve(
    host: str = typer.Option(settings.host, "--host"),
    port: int = typer.Option(settings.port, "--port"),
    reload: bool = typer.Option(False, "--reload"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser."),
) -> None:
    """Start the API and the web UI. Same as running `glossa` with no arguments."""
    launch(host, port, reload, open_browser)


@app.callback()
def default(
    ctx: typer.Context,
    host: str = typer.Option(settings.host, "--host"),
    port: int = typer.Option(settings.port, "--port"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the browser."),
) -> None:
    """Run with no subcommand to start the server and open the web UI."""
    if ctx.invoked_subcommand is None:
        launch(host, port, reload=False, open_browser=open_browser)


if __name__ == "__main__":
    app()
