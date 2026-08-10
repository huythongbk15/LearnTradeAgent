import type { Position, Trade } from "../api";

export function fmt(n: number | undefined | null, digits = 2): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtUsd(n: number | undefined | null): string {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  return `$${fmt(n, 2)}`;
}

export function pctClass(n: number): string {
  return n >= 0 ? "pos" : "neg";
}

export function PositionsTable({ positions }: { positions: Position[] }) {
  if (!positions.length) return <p className="muted">Không có vị thế đang mở.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Symbol</th>
          <th>Qty</th>
          <th>Avg Entry</th>
          <th>Current</th>
          <th>Market Value</th>
          <th>Unreal. PnL</th>
        </tr>
      </thead>
      <tbody>
        {positions.map((p) => (
          <tr key={p.symbol}>
            <td className="mono">{p.symbol}</td>
            <td className="mono">{fmt(p.qty, 5)}</td>
            <td className="mono">{fmtUsd(p.avg_price)}</td>
            <td className="mono">{fmtUsd(p.current_price)}</td>
            <td className="mono">{fmtUsd(p.market_value)}</td>
            <td>
              <span className={`pnl-chip ${p.pnl_pct >= 0 ? "pos" : "neg"}`}>
                {fmtUsd(p.unrealized_pnl)} ({p.pnl_pct >= 0 ? "+" : ""}
                {fmt(p.pnl_pct, 1)}%)
              </span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function TradesTable({ trades }: { trades: Trade[] }) {
  if (!trades.length) return <p className="muted">Chưa có giao dịch.</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Price</th>
          <th>PnL</th>
        </tr>
      </thead>
      <tbody>
        {trades.map((t, i) => (
          <tr key={i}>
            <td className="mono">{t.timestamp ?? "—"}</td>
            <td className="mono">{t.symbol}</td>
            <td>
              <span className={`badge ${(t.side || "").toLowerCase() === "buy" ? "green" : "red"}`}>
                {t.side}
              </span>
            </td>
            <td className="mono">{fmt(t.qty, 5)}</td>
            <td className="mono">{fmtUsd(t.price)}</td>
            <td className="mono">{t.pnl !== undefined ? fmtUsd(t.pnl) : "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}