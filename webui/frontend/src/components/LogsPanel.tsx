import { useEffect, useRef, useState } from "react";
import { logsTail } from "../api";

const LEVELS = ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"];

function levelOf(line: string): string | null {
  const m = line.match(/\|\s*(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*\|/);
  return m ? m[1] : null;
}

function levelColor(line: string): string {
  const lv = levelOf(line);
  if (lv === "ERROR" || lv === "CRITICAL") return "#ff5d5d";
  if (lv === "WARNING") return "#ffd166";
  if (lv === "DEBUG") return "#8d99ae";
  return "var(--muted)";
}

/** Tab Logs realtime — poll file log, terminal style. */
export function LogsPanel() {
  const [source, setSource] = useState<"trading" | "server">("trading");
  const [lines, setLines] = useState<string[]>([]);
  const [filter, setFilter] = useState<string>("ALL");
  const [search, setSearch] = useState("");
  const [pause, setPause] = useState(false);
  const [path, setPath] = useState("");
  const [error, setError] = useState<string | null>(null);
  const boxRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const r = await logsTail(400, source);
        if (!alive) return;
        setLines(r.lines);
        setPath(r.path);
        setError(r.error ?? null);
      } catch {
        /* giữ log cũ khi mạng lỗi nhẹ */
      }
    };
    poll();
    const id = window.setInterval(poll, 2000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [source]);

  // Auto-scroll nếu không pause
  useEffect(() => {
    const el = boxRef.current;
    if (el && !pause) el.scrollTop = el.scrollHeight;
  }, [lines, pause]);

  const visible = lines
    .filter((l) => (filter === "ALL" ? true : levelOf(l) === filter))
    .filter((l) => (search ? l.toLowerCase().includes(search.toLowerCase()) : true));

  const counts = {
    ALL: lines.length,
    ERROR: lines.filter((l) => levelOf(l) === "ERROR").length,
    WARNING: lines.filter((l) => levelOf(l) === "WARNING").length,
  };

  return (
    <div className="panel">
      <div className="row spread">
        <h2>📋 Logs realtime <small style={{ color: "var(--muted)", fontWeight: 400 }}>(tự làm mới 2s)</small></h2>
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option>ALL</option>
            {LEVELS.map((l) => <option key={l}>{l}</option>)}
          </select>
          <select value={source} onChange={(e) => setSource(e.target.value as "trading" | "server")}>
            <option value="trading">trading_agent.log</option>
            <option value="server">server.log</option>
          </select>
          <input
            placeholder="🔍 tìm kiếm…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ minWidth: 160 }}
          />
          <button className="ghost" onClick={() => setPause(!pause)}>
            {pause ? "▶ Auto-scroll" : "⏸ Dừng scroll"}
          </button>
          <button className="ghost" onClick={() => setLines([])}>🧹 Xoá màn hình</button>
        </div>
      </div>
      <p style={{ color: "var(--muted)", margin: "4px 0 8px", fontSize: 12 }}>
        <span className="mono">{path}</span> · {counts.ALL} dòng ·
        <span style={{ color: "#ff5d5d" }}> {counts.ERROR} ERROR</span> ·
        <span style={{ color: "#ffd166" }}> {counts.WARNING} WARNING</span>
      </p>
      {error && <p className="neg">Lỗi: {error}</p>}
      <pre
        ref={boxRef}
        className="joblog"
        style={{
          maxHeight: "60vh",
          overflow: "auto",
          fontFamily: "ui-monospace, monospace",
          fontSize: 12,
          lineHeight: 1.5,
          background: "#081420",
          border: "1px solid var(--line)",
          borderRadius: 8,
          padding: 10,
        }}
      >
        {visible.length === 0 && <span style={{ color: "var(--muted)" }}>— không có dòng nào khớp —</span>}
        {visible.map((l, i) => (
          <div key={i} style={{ color: levelColor(l), whiteSpace: "pre-wrap" }}>{l}</div>
        ))}
      </pre>
    </div>
  );
}