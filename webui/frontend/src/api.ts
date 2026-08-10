export interface SystemInfo {
  name: string;
  version: string;
  strategy_count: number;
  strategies: string[];
  symbols: string[];
  timeframes: string[];
  llm: { provider?: string; model?: string };
  alerts: { telegram?: { enabled?: boolean } };
}

export interface Position {
  symbol: string;
  qty: number;
  avg_price: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  pnl_pct: number;
}

export interface Portfolio {
  equity: number;
  cash: number;
  positions: Position[];
  source: string;
  note?: string;
}

export interface RiskState {
  equity: number;
  peak: number;
  drawdown_pct: number;
  max_drawdown_pct: number;
  trading_allowed: boolean;
  note?: string;
}

export interface Trade {
  symbol: string;
  side: string;
  qty: number;
  price: number;
  timestamp?: string;
  pnl?: number;
}

export interface Snapshot {
  ts: number;
  system: SystemInfo;
  portfolio: Portfolio;
  trades: { trades: Trade[]; note?: string };
  risk: { risk: RiskState };
}

export interface BacktestResult {
  strategy: string;
  symbol: string;
  timeframe: string;
  total_return?: number;
  sharpe?: number;
  profit_factor?: number;
  max_drawdown?: number;
  win_rate?: number;
  trades?: number;
  raw?: string;
}

export interface Job {
  status: "running" | "done" | "error";
  result?: BacktestResult | { output?: string; exit_code?: number; live?: boolean } | null;
  error?: string | null;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const getSystem = () => api<SystemInfo>("/api/system");
export const getPortfolio = () => api<Portfolio>("/api/portfolio");
export const getRisk = () => api<{ risk: RiskState }>("/api/risk");
export const getTrades = (limit = 20) =>
  api<{ trades: Trade[] }>(`/api/trades?limit=${limit}`);
export const startBacktest = (body: { strategy: string; symbol: string; timeframe: string }) =>
  api<{ job_id: string }>("/api/backtest", { method: "POST", body: JSON.stringify(body) });
export const getJob = (jobId: string) => api<Job>(`/api/backtest/${jobId}`);
export const closeAll = (reason = "webui_kill_switch") =>
  api<{ closed: boolean; error?: string }>("/api/positions/close", {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
export const liveRun = (live: boolean) =>
  api<{ job_id: string }>("/api/live/run", {
    method: "POST",
    body: JSON.stringify({ live }),
  });
export const liveStatus = () => api<{ ok: boolean; report?: string; error?: string }>("/api/live/status");