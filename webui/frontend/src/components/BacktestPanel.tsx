import { useEffect, useState } from "react";
import { getJob, startBacktest, type Job, type SystemInfo } from "../api";

export function BacktestPanel({ system }: { system: SystemInfo | null }) {
  const [strategy, setStrategy] = useState(system?.strategies?.[0] ?? "enhanced_ma");
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [timeframe, setTimeframe] = useState("1h");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    setRunning(true);
    const poll = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        setJob(j);
        if (j.status !== "running") {
          setRunning(false);
          window.clearInterval(poll);
        }
      } catch {
        window.clearInterval(poll);
        setRunning(false);
      }
    }, 2000);
    return () => window.clearInterval(poll);
  }, [jobId]);

  const run = async () => {
    setJob(null);
    const { job_id } = await startBacktest({ strategy, symbol, timeframe });
    setJobId(job_id);
  };

  const r = job?.result as { total_return?: number; sharpe?: number; profit_factor?: number; max_drawdown?: number; win_rate?: number; trades?: number; raw?: string } | null | undefined;

  return (
    <div className="panel">
      <h2>🔬 Backtest</h2>
      <div className="row">
        <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          {system?.strategies.map((s) => (
            <option key={s}>{s}</option>
          ))}
        </select>
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
        <button onClick={run} disabled={running}>
          {running ? "⏳ Đang chạy…" : "▶ Chạy Backtest"}
        </button>
      </div>
      {job?.status === "error" && <p className="neg">Lỗi: {job.error}</p>}
      {r?.total_return !== undefined && r?.total_return !== null && (
        <div className="grid" style={{ marginTop: 12, marginBottom: 0 }}>
          <div className="card">
            <h3>Total Return</h3>
            <div className={`metric ${r.total_return >= 0 ? "pos" : "neg"}`}>
              {r.total_return >= 0 ? "+" : ""}
              {fmtPct(r.total_return)}
            </div>
          </div>
          <div className="card">
            <h3>Sharpe</h3>
            <div className="metric">{fmt2(r.sharpe)}</div>
          </div>
          <div className="card">
            <h3>Profit Factor</h3>
            <div className="metric">{fmt2(r.profit_factor)}</div>
          </div>
          <div className="card">
            <h3>Max DD</h3>
            <div className="metric neg">{fmtPct(r.max_drawdown)}</div>
          </div>
          <div className="card">
            <h3>Win Rate</h3>
            <div className="metric">{fmtPct(r.win_rate)}</div>
          </div>
          <div className="card">
            <h3>Trades</h3>
            <div className="metric">{r.trades ?? "—"}</div>
          </div>
        </div>
      )}
      {r?.raw && <pre className="joblog">{r.raw}</pre>}
    </div>
  );
}

function fmt2(n: number | undefined | null): string {
  return n === undefined || n === null || Number.isNaN(n) ? "—" : n.toFixed(2);
}
function fmtPct(n: number | undefined | null): string {
  return n === undefined || n === null || Number.isNaN(n) ? "—" : `${(n * 100).toFixed(2)}%`;
}