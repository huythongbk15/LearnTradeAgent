import { useEffect, useState } from "react";
import { getJob, liveRun, liveStatus, closeAll, type Job } from "../api";

export function LivePanel() {
  const [live, setLive] = useState(false);
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
    setJob(null);
    const { job_id } = await liveRun(live);
    setJobId(job_id);
    setReport(null);
  };

  const status = async () => {
    const s = await liveStatus();
    setReport(s.ok ? (s.report ?? "") : `Lỗi: ${s.error ?? ""}`);
  };

  const kill = async () => {
    setClosing(true);
    try {
      const r = await closeAll();
      alert(r.closed ? "✅ Đã đóng toàn bộ vị thế (kill switch)" : `❌ ${r.error}`);
    } finally {
      setClosing(false);
    }
  };

  const out = job?.result as { output?: string; exit_code?: number } | null | undefined;

  return (
    <div className="panel">
      <div className="row spread">
        <h2>🚀 Live Trading</h2>
        <button className="danger" onClick={kill} disabled={closing}>
          {closing ? "⏳ Đang đóng…" : "🛑 Kill Switch — đóng tất cả vị thế"}
        </button>
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <label>
          <input type="checkbox" checked={live} onChange={(e) => setLive(e.target.checked)} /> Cho phép đặt lệnh (--live)
        </label>
        <button onClick={run} disabled={busy}>
          {busy ? "⏳ Đang chạy…" : "▶ Chạy live cycle"}
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