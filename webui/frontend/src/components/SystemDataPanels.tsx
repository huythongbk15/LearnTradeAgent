import { useEffect, useState } from "react";
import { CliJobPanel } from "./CliJob";
import { PieChart } from "./PieChart";
import { ProgressBar } from "./ProgressBar";
import { dataFetch, dataDatasets, llmCacheStats, metaRegimes, executionReset, systemDaily, systemHealth, backtestCompare, portfolioWeights, getJob, type BacktestCompareResult, type PortfolioWeightsResult } from "../api";

export const STRATEGIES = [
  "ma_crossover", "rsi", "bbands", "enhanced_ma", "ma_adx",
  "ma_vol_target", "ensemble_ma_adx", "ma_adx_regime", "regime_switching", "agent_ensemble",
];

const fmtBytes = (n: number) =>
  n > 1_000_000 ? `${(n / 1_000_000).toFixed(1)}MB` : n > 1000 ? `${(n / 1000).toFixed(0)}KB` : `${n}B`;

export function DataPanel({ symbols, timeframes }: { symbols: string[]; timeframes: string[] }) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "BTC/USDT");
  const [tf, setTf] = useState(timeframes.includes("1h") ? "1h" : timeframes[0] ?? "1h");
  const [since, setSince] = useState("2024-01-01");
  const [exchange, setExchange] = useState("binance");
  const [save, setSave] = useState(false);
  const [datasets, setDatasets] = useState<{ path: string; size: number }[]>([]);

  const load = async () => setDatasets((await dataDatasets()).datasets);
  useEffect(() => {
    load();
  }, []);

  return (
    <>
      <CliJobPanel
        title="Dữ liệu — Fetch OHLCV"
        icon="📦"
        description="Tải dữ liệu lịch sử về kho parquet (tích checkbox Lưu dữ liệu để dùng cho backtest)."
        run={() => dataFetch(symbol, tf, since, exchange, save)}
        fields={
          <>
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select value={tf} onChange={(e) => setTf(e.target.value)}>
              {timeframes.map((t) => <option key={t}>{t}</option>)}
            </select>
            <input value={since} onChange={(e) => setSince(e.target.value)} title="Ngày bắt đầu (ISO)" style={{ minWidth: 130 }} />
            <select value={exchange} onChange={(e) => setExchange(e.target.value)} title="Sàn giao dịch">
              <option value="binance">binance</option>
              <option value="binance_futures">binance_futures</option>
            </select>
            <label style={{ display: "flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
              <input type="checkbox" checked={save} onChange={(e) => setSave(e.target.checked)} />
              💾 Lưu dữ liệu
            </label>
          </>
        }
      />
      <div className="panel">
        <div className="row spread">
          <h2>🗂 Dataset đã lưu ({datasets.length})</h2>
          <button className="ghost" onClick={load}>🔄 Refresh</button>
        </div>
        <table>
          <thead>
            <tr><th>Path</th><th>Size</th></tr>
          </thead>
          <tbody>
            {datasets.slice(0, 20).map((d) => (
              <tr key={d.path}>
                <td className="mono">{d.path}</td>
                <td>{fmtBytes(d.size)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function PortfolioPanel({ symbols }: { symbols: string[] }) {
  const methods = ["max_sharpe", "min_variance", "mean_variance", "hrp", "black_litterman", "risk_parity", "max_div", "equal_weight"];
  const [selected, setSelected] = useState(symbols.slice(0, 3).join(", "));
  const [method, setMethod] = useState("max_sharpe");
  const [jobId, setJobId] = useState<string | null>(null);
  const [res, setRes] = useState<PortfolioWeightsResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ pct: number; stage: string } | null>(null);

  useEffect(() => {
    if (!jobId) return;
    setBusy(true);
    const poll = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        setProgress(j.progress ?? null);
        if (j.status === "done") {
          const r = j.result as PortfolioWeightsResult;
          setRes(r);
          setErr(r.error ?? null);
          setBusy(false);
          window.clearInterval(poll);
        } else if (j.status === "error") {
          setErr(j.error ?? "lỗi");
          setBusy(false);
          window.clearInterval(poll);
        }
      } catch {
        window.clearInterval(poll);
        setBusy(false);
      }
    }, 2000);
    return () => window.clearInterval(poll);
  }, [jobId]);

  const run = async () => {
    setRes(null);
    setErr(null);
    const sel = selected.split(",").map((s) => s.trim()).filter(Boolean);
    const { job_id } = await portfolioWeights(sel, method);
    setJobId(job_id);
  };

  return (
    <div className="panel">
      <div className="row spread">
        <h2>📈 Portfolio Optimizer</h2>
        <div className="row">
          <input value={selected} onChange={(e) => setSelected(e.target.value)} title="Symbols phân tách bằng dấu phẩy" style={{ minWidth: 280 }} />
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            {methods.map((m) => <option key={m}>{m}</option>)}
          </select>
          <button onClick={run} disabled={busy}>{busy ? "⏳ Đang tối ưu…" : "▶ Tối ưu"}</button>
        </div>
      </div>
      <p style={{ color: "var(--muted)", marginBottom: 8 }}>
        Tối ưu tỷ trọng danh mục theo 8 phương pháp (max_sharpe, HRP, Black-Litterman, risk_parity…).
      </p>
      <ProgressBar progress={progress} />
      {err && <p className="neg">Lỗi: {err}</p>}
      {res && !res.error && (
        <div className="row" style={{ gap: 24, flexWrap: "wrap", marginTop: 6 }}>
          <PieChart labels={res.symbols} values={res.weights} />
          <div style={{ minWidth: 260 }}>
            <h3 style={{ marginBottom: 8 }}>📊 Metrics ({res.method})</h3>
            <table>
              <tbody>
                <tr><td>Expected Return</td><td className="pos">{(res.expected_return * 100).toFixed(2)}% /yr</td></tr>
                <tr><td>Expected Volatility</td><td>{(res.expected_volatility * 100).toFixed(2)}%</td></tr>
                <tr><td>Sharpe Ratio</td><td className="pos">{res.sharpe_ratio.toFixed(2)}</td></tr>
                <tr><td>Diversification</td><td>{res.diversification_ratio.toFixed(2)}</td></tr>
                <tr><td>VaR (95%)</td><td className="neg">{(res.var_95 * 100).toFixed(2)}%</td></tr>
                <tr><td>CVaR (95%)</td><td className="neg">{(res.cvar_95 * 100).toFixed(2)}%</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export function BacktestComparePanel({
  symbols,
  timeframes,
  strategies,
}: {
  symbols: string[];
  timeframes: string[];
  strategies: string[];
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({
    ma_crossover: true,
    rsi: true,
    bbands: false,
    enhanced_ma: true,
    ma_adx: false,
    ma_vol_target: false,
    ensemble_ma_adx: false,
    ma_adx_regime: false,
    regime_switching: false,
    agent_ensemble: false,
  });
  const [symbol, setSymbol] = useState(symbols[0] ?? "BTC/USDT");
  const [tf, setTf] = useState(timeframes.includes("1h") ? "1h" : timeframes[0] ?? "1h");
  const [jobId, setJobId] = useState<string | null>(null);
  const [res, setRes] = useState<BacktestCompareResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<{ pct: number; stage: string } | null>(null);

  useEffect(() => {
    if (!jobId) return;
    setBusy(true);
    const poll = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        setProgress(j.progress ?? null);
        if (j.status === "done") {
          setRes(j.result as BacktestCompareResult);
          setBusy(false);
          window.clearInterval(poll);
        } else if (j.status === "error") {
          setErr(j.error ?? "lỗi");
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

  const selected = strategies.filter((s) => checked[s]);

  const run = async () => {
    if (selected.length === 0) return;
    setRes(null);
    setErr(null);
    const { job_id } = await backtestCompare(selected, symbol, tf);
    setJobId(job_id);
  };

  const bestReturn = res ? Math.max(...res.rows.map((r) => r.total_return_pct)) : 0;
  const bestSharpe = res ? Math.max(...res.rows.map((r) => r.sharpe_ratio)) : 0;
  const bestPF = res ? Math.max(...res.rows.map((r) => r.profit_factor)) : 0;

  return (
    <div className="panel">
      <div className="row spread">
        <h2>⚖️ So sánh nhiều strategy</h2>
        <div className="row">
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
            {symbols.map((s) => <option key={s}>{s}</option>)}
          </select>
          <select value={tf} onChange={(e) => setTf(e.target.value)}>
            {timeframes.map((t) => <option key={t}>{t}</option>)}
          </select>
          <button onClick={run} disabled={busy || selected.length === 0}>
            {busy ? "⏳ Đang backtest…" : "▶ So sánh"}
          </button>
        </div>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", margin: "8px 0" }}>
        {strategies.map((s) => (
          <label key={s} className="row" style={{ gap: 5, alignItems: "center", fontSize: 13 }}>
            <input type="checkbox" checked={!!checked[s]} onChange={(e) => setChecked({ ...checked, [s]: e.target.checked })} />
            {s}
          </label>
        ))}
      </div>
      <ProgressBar progress={progress} />
      {err && <p className="neg">Lỗi: {err}</p>}
      {res && (
        <>
          <p style={{ color: "var(--muted)", margin: "4px 0 8px" }}>
            {res.symbol} {res.timeframe} · {res.rows.length} strategy
          </p>
          <table>
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Return</th>
                <th>Ann.</th>
                <th>Sharpe</th>
                <th>Sortino</th>
                <th>Max DD</th>
                <th>Win Rate</th>
                <th>PF</th>
                <th>Trades</th>
                <th>Calmar</th>
              </tr>
            </thead>
            <tbody>
              {res.rows.map((r) => (
                <tr key={r.strategy}>
                  <td className="mono">{r.strategy}</td>
                  <td className={r.total_return_pct >= 0 ? "pos" : "neg"}>
                    {r.total_return_pct >= bestReturn ? "🏆 " : ""}{r.total_return_pct.toFixed(2)}%
                  </td>
                  <td>{r.annualized_return_pct.toFixed(2)}%</td>
                  <td className={r.sharpe_ratio >= bestSharpe ? "pos" : ""}>
                    {r.sharpe_ratio === bestSharpe ? "🏆 " : ""}{r.sharpe_ratio.toFixed(2)}
                  </td>
                  <td>{r.sortino_ratio.toFixed(2)}</td>
                  <td className="neg">{r.max_drawdown_pct.toFixed(2)}%</td>
                  <td>{(r.win_rate * 100).toFixed(1)}%</td>
                  <td>{r.profit_factor === bestPF ? "🏆 " : ""}{r.profit_factor.toFixed(2)}</td>
                  <td>{r.total_trades}</td>
                  <td>{r.calmar_ratio.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.keys(res.errors).length > 0 && (
            <p className="neg" style={{ marginTop: 8, fontSize: 13 }}>
              ❌ {Object.entries(res.errors).map(([k, v]) => `${k}: ${v}`).join(" | ")}
            </p>
          )}
        </>
      )}
    </div>
  );
}

export function SystemPanel() {
  return (
    <>
      <div className="panel">
        <h2>🏥 Health Check</h2>
        <CliJobShort title="Kiểm tra toàn bộ components" run={systemHealth} />
      </div>
      <CliJobPanel title="Daily Summary" icon="📅" description="Hiệu suất hôm nay (equity, P&L, giao dịch)." run={systemDaily} />
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <CliJobPanel title="LLM Cache Stats" icon="🧠" description="Thống kê cache & chi phí LLM." run={llmCacheStats} />
        <CliJobPanel title="Meta — Regimes" icon="🌦" description="Phân tích regime (bull/bear/sideways) trong dữ liệu." run={metaRegimes} />
      </div>
      <CliJobPanel
        title="Execution — Reset Paper Exchange"
        icon="♻️"
        description="Đặt lại trạng thái paper exchange nội bộ về ban đầu."
        run={() => {
          if (!window.confirm("Xóa toàn bộ trạng thái Paper Exchange nội bộ?")) {
            return Promise.reject(new Error("Đã hủy thao tác reset"));
          }
          return executionReset();
        }}
      />
    </>
  );
}

function CliJobShort({ title, run }: { title: string; run: () => Promise<any> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const r = await run();
      setText(r.stdout || r.stderr || "(trống)");
    } catch (e) {
      setText(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row">
      <button className="ghost" onClick={go} disabled={busy}>{busy ? "⏳" : "▶"} {title}</button>
      {text && <pre className="joblog" style={{ maxHeight: 180, flex: "1 1 100%" }}>{text}</pre>}
    </div>
  );
}
