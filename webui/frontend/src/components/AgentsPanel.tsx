import { useEffect, useState } from "react";
import { agentsAnalyze, getJob, type AnalyzeResult, type SystemInfo } from "../api";

export function AgentsPanel({ system }: { system: SystemInfo | null }) {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<AnalyzeResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    setBusy(true);
    const poll = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        if (j.status === "done" && j.result && (j.result as AnalyzeResult).decision) {
          setResult(j.result as AnalyzeResult);
          setBusy(false);
          window.clearInterval(poll);
        } else if (j.status === "error") {
          setError(j.error ?? "lỗi không xác định");
          setBusy(false);
          window.clearInterval(poll);
        } else if (j.status === "done") {
          setBusy(false);
          window.clearInterval(poll);
        }
      } catch {
        window.clearInterval(poll);
        setBusy(false);
      }
    }, 2500);
    return () => window.clearInterval(poll);
  }, [jobId]);

  const run = async () => {
    setResult(null);
    setError(null);
    const { job_id } = await agentsAnalyze(symbol, timeframe);
    setJobId(job_id);
  };

  const signalClass = (s: string) =>
    s === "BUY" ? "pos" : s === "SELL" ? "neg" : "muted";

  return (
    <div className="panel">
      <div className="row spread">
        <h2>🤖 AI Agents — phân tích 4 agent</h2>
        <div className="row">
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {system?.symbols.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
            {system?.timeframes.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <button onClick={run} disabled={busy}>
            {busy ? "⏳ Đang phân tích…" : "🔎 Phân tích"}
          </button>
        </div>
      </div>
      <p style={{ color: "var(--muted)", marginBottom: 8 }}>
        Flow: Technical → Sentiment → Risk → Trader · LLM: {system?.llm.provider} ({system?.llm.model})
      </p>
      {error && <p className="neg">Lỗi: {error}</p>}
      <div className="row" style={{ marginTop: 8 }}>
        {result?.agents.map((a) => (
          <div className="card" key={a.name} style={{ minWidth: 180, flex: 1 }}>
            <h3>{a.name.replace("_", " ")}</h3>
            <div className={`metric ${signalClass(a.signal)}`} style={{ fontSize: 17 }}>
              {a.signal}
              <small> · {(a.confidence ?? 0) * 100}%</small>
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 6 }}>
              {a.reasoning ?? "—"}
            </div>
          </div>
        ))}
      </div>
      {result?.decision && (
        <div className="card" style={{ marginTop: 10, borderColor: "var(--accent)" }}>
          <h3>🎯 Quyết định cuối (weighted vote + risk override)</h3>
          <div className="row" style={{ gap: 14 }}>
            <span className={`metric ${signalClass(result.decision.signal)}`}>
              {result.decision.signal} · {(result.decision.confidence * 100).toFixed(0)}%
            </span>
            <span style={{ color: "var(--muted)" }}>
              {result.symbol} {result.timeframe} @ ${result.current_price?.toLocaleString("en-US", { maximumFractionDigits: 2 })}
            </span>
          </div>
          {result.decision.reasoning && (
            <p style={{ fontSize: 13, marginTop: 6 }}>{result.decision.reasoning}</p>
          )}
        </div>
      )}
    </div>
  );
}