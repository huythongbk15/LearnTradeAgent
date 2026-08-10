import { useState } from "react";
import { useRealtime } from "./ws";
import { EquityChart } from "./components/EquityChart";
import { PositionsTable, TradesTable, fmtUsd } from "./components/Tables";
import { BacktestPanel } from "./components/BacktestPanel";
import { LivePanel } from "./components/LivePanel";
import { LogsPanel } from "./components/LogsPanel";
import { AgentsPanel } from "./components/AgentsPanel";
import { DataPanel, PortfolioPanel, SystemPanel, BacktestComparePanel, STRATEGIES } from "./components/SystemDataPanels";

type TabKey = "dashboard" | "agents" | "backtest" | "data" | "portfolio" | "live" | "logs" | "system";

const TABS: { key: TabKey; label: string }[] = [
  { key: "dashboard", label: "📊 Dashboard" },
  { key: "agents", label: "🤖 AI Agents" },
  { key: "backtest", label: "🔬 Backtest" },
  { key: "data", label: "📦 Dữ liệu" },
  { key: "portfolio", label: "📈 Portfolio" },
  { key: "live", label: "🚀 Live" },
  { key: "logs", label: "📋 Logs" },
  { key: "system", label: "⚙️ Hệ thống" },
];

export default function App() {
  const [tab, setTab] = useState<TabKey>("dashboard");
  const { connected, last, history } = useRealtime();
  const sys = last?.system ?? null;
  const pf = last?.portfolio ?? null;
  const risk = last?.risk.risk ?? null;
  const trades = last?.trades.trades ?? [];

  const ddOk = risk ? risk.drawdown_pct < risk.max_drawdown_pct : true;
  const symbols = sys?.symbols ?? ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT"];
  const timeframes = sys?.timeframes ?? ["1h", "4h", "1d"];

  return (
    <div className="app">
      <header>
        <div>
          <h1>📊 Trading Agent System</h1>
          <div className="sub">
            {sys?.name} v{sys?.version} · LLM: {sys?.llm.provider ?? "—"} ({sys?.llm.model ?? ""}) · Telegram:{" "}
            {sys?.alerts.telegram_configured ? "đã cấu hình" : "chưa cấu hình"}
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

      <nav style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 14 }}>
        {TABS.map((t) => (
          <button
            key={t.key}
            className="ghost"
            style={
              tab === t.key
                ? { background: "var(--accent)", color: "#04121f", fontWeight: 700 }
                : undefined
            }
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "dashboard" && (
        <>
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
        </>
      )}

      {tab === "agents" && <AgentsPanel system={sys} />}
      {tab === "backtest" && (
        <>
          <BacktestComparePanel symbols={symbols} timeframes={timeframes} strategies={STRATEGIES} />
          <BacktestPanel system={sys} />
        </>
      )}
      {tab === "data" && <DataPanel symbols={symbols} timeframes={timeframes} />}
      {tab === "portfolio" && <PortfolioPanel symbols={symbols} />}
      {tab === "live" && <LivePanel />}
      {tab === "logs" && <LogsPanel />}
      {tab === "system" && <SystemPanel />}
    </div>
  );
}
