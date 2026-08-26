import { useCallback, useEffect, useState } from "react";
import { api, type Doc, type Health } from "./api";
import { Chat } from "./components/Chat";
import { Sidebar } from "./components/Sidebar";
import { MoonIcon, SunIcon } from "./components/Icons";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [models, setModels] = useState<string[]>([]);
  const [profile, setProfile] = useState("balanced");
  const [model, setModel] = useState("");
  const [topK, setTopK] = useState(5);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState(() => localStorage.getItem("theme") ?? "dark");

  const refresh = useCallback(async () => {
    try {
      const [state, documents] = await Promise.all([api.health(), api.documents()]);
      setHealth(state);
      setDocs(documents);
      setProfile((current) => current || state.profiles[0]?.name);
    } catch (exception) {
      setError((exception as Error).message);
    }
  }, []);

  useEffect(() => {
    void refresh();
    void api
      .models()
      .then((result) => {
        setModels(result.available);
        setModel((current) => current || result.current);
      })
      .catch(() => undefined);
  }, [refresh]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);

  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), 8000);
    return () => clearTimeout(timer);
  }, [error]);

  const active = health?.profiles.find((item) => item.name === profile);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">Gl</span>
          <div>
            <h1>Glossa</h1>
            <p>
              {health
                ? `${health.documents} documents · ${health.chunks} chunks · ${Object.keys(health.languages).length} languages`
                : "connecting…"}
            </p>
          </div>
        </div>

        <div className="controls">
          <label className="control">
            <span>Mode</span>
            <select value={profile} onChange={(event) => setProfile(event.target.value)}>
              {health?.profiles.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>

          <label className="control">
            <span>Model</span>
            <select value={model} onChange={(event) => setModel(event.target.value)}>
              {models.length === 0 && <option value="">{health?.llm_model ?? "—"}</option>}
              {models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </label>

          <label className="control slider">
            <span>Chunks: {topK}</span>
            <input
              type="range"
              min={1}
              max={15}
              value={topK}
              onChange={(event) => setTopK(Number(event.target.value))}
            />
          </label>

          <span
            className={`status ${health?.llm_reachable ? "on" : "off"}`}
            title={`${health?.provider ?? ""} · ${health?.llm_base_url ?? ""}`}
          >
            {health?.provider ?? "llm"} {health?.llm_reachable ? "ready" : "offline"}
            {health?.llm_local === false && " · remote"}
          </span>

          <button
            className="icon-button"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            title="Theme"
          >
            {theme === "dark" ? <SunIcon /> : <MoonIcon />}
          </button>
        </div>
      </header>

      {active && <p className="profile-hint">{active.description}</p>}

      {error && (
        <div className="toast" role="alert">
          {error}
          <button onClick={() => setError(null)}>×</button>
        </div>
      )}

      <main className="layout">
        <Sidebar
          docs={docs}
          selected={selected}
          onSelect={setSelected}
          onChanged={() => void refresh()}
          onError={setError}
        />
        <Chat
          docCount={docs.length}
          selected={selected}
          profile={profile}
          model={model}
          topK={topK}
          onError={setError}
        />
      </main>
    </div>
  );
}
