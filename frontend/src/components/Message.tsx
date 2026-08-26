import { Fragment, type ReactNode, useState } from "react";
import { api, type Answer, type Source } from "../api";
import { ChevronIcon, DownloadIcon } from "./Icons";

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  text: string;
  question?: string;
  sources?: Source[];
  meta?: Answer["meta"];
  streaming?: boolean;
  error?: boolean;
}

/**
 * Minimal inline formatter: bold, inline code, and [n] citation chips.
 * A full Markdown dependency is not worth it for answers this short.
 */
function formatInline(text: string, onCite?: (ref: number) => void): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\*\*[^*]+\*\*|`[^`]+`|\[\d+(?:\]\[\d+)*\])/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("**")) {
      nodes.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("`")) {
      nodes.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      for (const ref of token.match(/\d+/g) ?? []) {
        nodes.push(
          <button key={key++} className="cite" onClick={() => onCite?.(Number(ref))}>
            {ref}
          </button>,
        );
      }
    }
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function formatBlocks(text: string, onCite?: (ref: number) => void): ReactNode {
  return text.split(/\n{2,}/).map((block, index) => {
    const lines = block.split("\n");
    const bullets = lines.every((line) => /^\s*([-*•]|\d+[.)])\s+/.test(line));
    if (bullets) {
      return (
        <ul key={index}>
          {lines.map((line, i) => (
            <li key={i}>{formatInline(line.replace(/^\s*([-*•]|\d+[.)])\s+/, ""), onCite)}</li>
          ))}
        </ul>
      );
    }
    return (
      <p key={index}>
        {lines.map((line, i) => (
          <Fragment key={i}>
            {i > 0 && <br />}
            {formatInline(line, onCite)}
          </Fragment>
        ))}
      </p>
    );
  });
}

export function Message({ turn }: { turn: ChatTurn }) {
  const [open, setOpen] = useState<number | null>(null);

  if (turn.role === "user") {
    return (
      <div className="turn user">
        <div className="bubble">{turn.text}</div>
      </div>
    );
  }

  const answer: Answer = {
    question: turn.question ?? "",
    answer: turn.text,
    sources: turn.sources ?? [],
    meta: turn.meta ?? {
      model: "",
      profile: "",
      language: "",
      chunks_used: 0,
      elapsed_ms: 0,
    },
  };

  return (
    <div className="turn assistant">
      <div className={`bubble${turn.error ? " error" : ""}`}>
        {turn.text ? formatBlocks(turn.text, setOpen) : <span className="typing" />}
        {turn.streaming && turn.text && <span className="caret" />}
      </div>

      {/* Sources arrive before the first token, so show them straight away --
          on a slow local model that is the only feedback for a long minute. */}
      {turn.sources && turn.sources.length > 0 && (
        <div className="sources">
          {turn.sources.map((source) => (
            <div key={source.ref} className={`source${open === source.ref ? " open" : ""}`}>
              <button
                className="source-head"
                onClick={() => setOpen(open === source.ref ? null : source.ref)}
              >
                <span className="ref">{source.ref}</span>
                <span className="source-name">{source.filename}</span>
                {source.location && <span className="source-where">{source.location}</span>}
                <span className="score">{source.score.toFixed(3)}</span>
                <ChevronIcon />
              </button>
              {open === source.ref && <div className="source-text">{source.text}</div>}
            </div>
          ))}
        </div>
      )}

      {!turn.streaming && turn.meta && (
        <div className="turn-foot">
          <span>
            {turn.meta.model} · {turn.meta.profile} · {turn.meta.chunks_used} chunks ·{" "}
            {(turn.meta.elapsed_ms / 1000).toFixed(1)} s
          </span>
          <div className="turn-actions">
            <button onClick={() => void api.exportAnswer(answer, "markdown")}>
              <DownloadIcon /> MD
            </button>
            <button onClick={() => void api.exportAnswer(answer, "json")}>
              <DownloadIcon /> JSON
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
