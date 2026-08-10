import { useEffect, useState } from "react";
import { getJob, liveRun, liveStatus, closeAll, type Job } from "../api";

export function LivePanel() {
  const [confirmed, setConfirmed] = useState(false);
  const [apiKey, setApiKey] = useState(() => window.localStorage.getItem("trading_api_key") ?? "");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!jobId) return;
    setBusy(true);
    const poll = window.setInterval(async () => {
      try {
        const j = await getJob(jobId);
        setJob(j);
        if (j.status !== "running") {
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
    if (!confirmed) {
      alert("Hãy xác nhận đây là một chu kỳ Alpaca Paper trước khi chạy.");
      return;
    }
    setJob(null);
    try {
      const { job_id } = await liveRun();
      setJobId(job_id);
      setReport(null);
    } catch (error) {
      alert(error instanceof Error ? error.message : String(error));
    }
  };

  const status = async () => {
    try {
      const s = await liveStatus();
      setReport(s.ok ? (s.report ?? "") : `Lỗi: ${s.error ?? ""}`);
    } catch (error) {
      setReport(error instanceof Error ? error.message : String(error));
    }
  };

  const kill = async () => {
    if (!window.confirm("Đóng toàn bộ vị thế và hủy lệnh trên tài khoản Alpaca Paper?")) return;
    setClosing(true);
    try {
      const r = await closeAll();
      alert(r.closed
        ? "✅ Đã gửi lệnh đóng và xác minh không còn vị thế Paper"
        : `❌ ${r.error ?? `Còn lại: ${(r.remaining ?? []).join(", ")}`}`);
    } catch (error) {
      alert(error instanceof Error ? error.message : String(error));
    } finally {
      setClosing(false);
    }
  };

  const saveApiKey = (value: string) => {
    setApiKey(value);
    if (value.trim()) window.localStorage.setItem("trading_api_key", value.trim());
    else window.localStorage.removeItem("trading_api_key");
  };

  const out = job?.result as { output?: string; exit_code?: number } | null | undefined;

  return (
    <div className="panel">
      <div className="row spread">
        <h2>🧪 Alpaca Paper Trading</h2>
        <button className="danger" onClick={kill} disabled={closing}>
          {closing ? "⏳ Đang đóng…" : "🛑 Kill Switch — đóng tất cả vị thế"}
        </button>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <label>
          Khóa quản trị
          <input
            type="password"
            value={apiKey}
            onChange={(e) => saveApiKey(e.target.value)}
            autoComplete="off"
            placeholder="WEBUI_API_KEY"
            style={{ marginLeft: 8 }}
          />
        </label>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <label>
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          /> Xác nhận chạy một chu kỳ trên tài khoản Paper
        </label>
        <button onClick={run} disabled={busy}>
          {busy ? "⏳ Đang chạy…" : "▶ Chạy Paper cycle"}
        </button>
        <button className="ghost" onClick={status}>
          📄 Status report
        </button>
      </div>
      {job?.status === "error" && <p className="neg">Lỗi: {job.error}</p>}
      {out?.output && <pre className="joblog">{out.output}</pre>}
      {report && <pre className="joblog">{report}</pre>}
    </div>
  );
}
