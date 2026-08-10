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
  progress?: { pct: number; stage: string } | null;
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

// --- Mở rộng: Agents / Data / Portfolio / System / LLM / Meta / Execution ---
export interface AgentSignal {
  name: string;
  signal: string;
  confidence: number | null;
  reasoning?: string;
  details?: Record<string, string>;
}
export interface AnalyzeResult {
  symbol: string;
  timeframe: string;
  current_price: number;
  decision: { signal: string; confidence: number; reasoning?: string };
  agents: AgentSignal[];
}
export interface CliResult {
  exit_code: number;
  stdout: string;
  stderr: string;
}

export const agentsAnalyze = (symbol: string, timeframe: string) =>
  api<{ job_id: string }>("/api/agents/analyze", {
    method: "POST",
    body: JSON.stringify({ symbol, timeframe }),
  });
export const dataFetch = (symbol: string, timeframe: string, since?: string) =>
  api<{ job_id: string }>("/api/data/fetch", {
    method: "POST",
    body: JSON.stringify({ symbol, timeframe, since }),
  });
export const dataDatasets = () => api<{ datasets: { path: string; size: number }[] }>("/api/data/datasets");
export const portfolioOptimize = (symbols: string[], method: string, lookback = 90) =>
  api<{ job_id: string }>("/api/portfolio/optimize", {
    method: "POST",
    body: JSON.stringify({ symbols, method, lookback }),
  });
export const systemDaily = () => api<CliResult>("/api/system/daily");
export const systemHealth = () => api<CliResult>("/api/system/health");
export const llmCacheStats = () => api<CliResult>("/api/llm/cache-stats");
export const metaRegimes = () => api<CliResult>("/api/meta/regimes");
export const executionReset = () => api<CliResult>("/api/execution/reset", { method: "POST" });

// --- So sánh backtest + portfolio weights JSON ---
export interface BacktestCompareRow {
  strategy: string;
  params?: Record<string, string>;
  total_return_pct: number;
  annualized_return_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown_pct: number;
  win_rate: number;
  profit_factor: number;
  total_trades: number;
  calmar_ratio: number;
  avg_hold_bars: number;
}
export interface BacktestCompareResult {
  rows: BacktestCompareRow[];
  errors: Record<string, string>;
  symbol: string;
  timeframe: string;
}
export const backtestCompare = (strategies: string[], symbol: string, timeframe: string) =>
  api<{ job_id: string }>("/api/backtest/compare", {
    method: "POST",
    body: JSON.stringify({ strategies, symbol, timeframe }),
  });
export interface PortfolioWeightsResult {
  method: string;
  symbols: string[];
  weights: number[];
  expected_return: number;
  expected_volatility: number;
  sharpe_ratio: number;
  diversification_ratio: number;
  var_95: number;
  cvar_95: number;
  error?: string;
}
export const portfolioWeights = (symbols: string[], method: string, lookback = 90) =>
  api<{ job_id: string }>("/api/portfolio/weights", {
    method: "POST",
    body: JSON.stringify({ symbols, method, lookback }),
  });