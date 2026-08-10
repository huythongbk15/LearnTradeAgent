import { useEffect, useState } from "react";
import { CliJobPanel } from "./CliJob";
import { dataFetch, dataDatasets, portfolioOptimize, llmCacheStats, metaRegimes, executionReset, systemDaily, systemHealth } from "../api";

const fmtBytes = (n: number) =>
  n > 1_000_000 ? `${(n / 1_000_000).toFixed(1)}MB` : n > 1000 ? `${(n / 1000).toFixed(0)}KB` : `${n}B`;

export function DataPanel({ symbols, timeframes }: { symbols: string[]; timeframes: string[] }) {
  const [symbol, setSymbol] = useState(symbols[0] ?? "BTC/USDT");
  const [tf, setTf] = useState(timeframes[0] ?? "1h");
  const [since, setSince] = useState("2024-01-01");
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
        description="Tải dữ liệu lịch sử từ Binance về kho parquet."
        run={() => dataFetch(symbol, tf, since)}
        fields={
          <>
            <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              {symbols.map((s) => <option key={s}>{s}</option>)}
            </select>
            <select value={tf} onChange={(e) => setTf(e.target.value)}>
              {timeframes.map((t) => <option key={t}>{t}</option>)}
            </select>
            <input value={since} onChange={(e) => setSince(e.target.value)} title="Ngày bắt đầu (ISO)" />
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
  const sel = () => selected.split(",").map((s) => s.trim()).filter(Boolean);

  return (
    <CliJobPanel
      title="Portfolio Optimizer"
      icon="📈"
      description="Tối ưu tỷ trọng danh mục theo 8 phương pháp (max_sharpe, HRP, Black-Litterman, risk_parity…)."
      run={() => portfolioOptimize(sel(), method)}
      fields={
        <>
          <input value={selected} onChange={(e) => setSelected(e.target.value)} title="Symbols phân tách bằng dấu phẩy" style={{ minWidth: 280 }} />
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            {methods.map((m) => <option key={m}>{m}</option>)}
          </select>
        </>
      }
    />
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
        run={executionReset}
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