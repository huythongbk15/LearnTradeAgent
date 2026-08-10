import { useRealtime } from "./ws";
import { EquityChart } from "./components/EquityChart";
import { PositionsTable, TradesTable, fmtUsd } from "./components/Tables";
import { BacktestPanel } from "./components/BacktestPanel";
import { LivePanel } from "./components/LivePanel";

export default function App() {
  const { connected, last, history } = useRealtime();
  const sys = last?.system ?? null;
  const pf = last?.portfolio ?? null;
  const risk = last?.risk.risk ?? null;
  const trades = last?.trades.trades ?? [];

  const ddOk = risk ? risk.drawdown_pct < risk.max_drawdown_pct : true;

  return (
    <div className="app">
      <header>
        <div>
          <h1>📊 Trading Agent System</h1>
          <div className="sub">
            {sys?.name} v{sys?.version} · LLM: {sys?.llm.provider ?? "—"} ({sys?.llm.model ?? ""}) · Telegram:{" "}
            {sys?.alerts.telegram?.enabled ? "bật" : "tắt"}
          </div>
        </div>
        <div className="row">
          <span className={`badge ${connected ? "green" : "red"}`}>
            <span className={`status-dot ${connected ? "on" : "off"}`} />
            {connected ? "Realtime" : "Đang kết nối…"}
          </span>
          {risk && (
            <span className={`badge ${ddOk ? "green" : "yellow"}`}>
              DD {risk.drawdown_pct.toFixed(2)}% {ddOk ? "✅" : "⚠️"}
            </span>
          )}
        </div>
      </header>

      <div className="grid">
        <div className="card">
          <h3>Equity</h3>
          <div className="metric">{fmtUsd(pf?.equity)}</div>
        </div>
        <div className="card">
          <h3>Cash</h3>
          <div className="metric">{fmtUsd(pf?.cash)}</div>
        </div>
        <div className="card">
          <h3>Vị thế mở</h3>
          <div className="metric">{pf?.positions.length ?? 0}</div>
        </div>
        <div className="card">
          <h3>Peak</h3>
          <div className="metric">{fmtUsd(risk?.peak)}</div>
        </div>
        <div className="card">
          <h3>Drawdown</h3>
          <div className={`metric ${risk && risk.drawdown_pct > 0 ? "neg" : "pos"}`}>
            {risk ? `${risk.drawdown_pct.toFixed(2)}%` : "—"}
            <small> / max {risk?.max_drawdown_pct.toFixed(0)}%</small>
          </div>
        </div>
        <div className="card">
          <h3>Trading</h3>
          <div className="metric">
            <span className={`badge ${risk?.trading_allowed ? "green" : "red"}`}>
              {risk?.trading_allowed ? "ALLOWED" : "BLOCKED"}
            </span>
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>📈 Equity (realtime)</h2>
        <EquityChart data={history} />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <div className="panel">
          <h2>💼 Positions ({pf?.positions.length ?? 0})</h2>
          <PositionsTable positions={pf?.positions ?? []} />
        </div>
        <div className="panel">
          <h2>📋 Trades gần đây ({trades.length})</h2>
          <TradesTable trades={trades} />
        </div>
      </div>

      <BacktestPanel system={sys} />
      <LivePanel />
    </div>
  );
}