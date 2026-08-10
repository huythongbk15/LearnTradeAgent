import { useEffect, useState } from "react";
import { getJob, type Job } from "../api";
import { ProgressBar } from "./ProgressBar";

/** Panel chạy job CLI chung: nút chạy + poll job + hiện stdout/stderr. */
export function CliJobPanel({
  title,
  icon,
  description,
  fields,
  run,
  defaultStdout,
}: {
  title: string;
  icon: string;
  description?: string;
  fields?: React.ReactNode;
  run: () => Promise<{ job_id: string } | { stdout?: string; stderr?: string; exit_code?: number }>;
  defaultStdout?: string;
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
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

  const start = async () => {
    setJob(null);
    try {
      const res = await run();
      if ("job_id" in res) {
        setJobId(res.job_id);
      } else {
        setJob({ status: "done", result: res, error: null });
        setBusy(false);
      }
    } catch (e) {
      setJob({ status: "error", error: String(e), result: null });
      setBusy(false);
    }
  };

  const out = job?.result as { stdout?: string; stderr?: string; exit_code?: number } | null | undefined;
  const text = out?.stdout || out?.stderr || "";
  const starting = job?.status === "running";
  // Stream log realtime khi job đang chạy (nếu có lines)
  const liveText = (job?.lines ?? []).join("\n");

  return (
    <div className="panel">
      <div className="row spread">
        <h2>
          {icon} {title}
        </h2>
        <button onClick={start} disabled={busy || starting}>
          {starting || busy ? "⏳ Đang chạy…" : "▶ Chạy"}
        </button>
      </div>
      {description && <p style={{ color: "var(--muted)", marginBottom: 8 }}>{description}</p>}
      {fields && <div className="row" style={{ marginBottom: 10 }}>{fields}</div>}
      <ProgressBar progress={job?.progress} />
      {job?.status === "error" && <p className="neg">Lỗi: {job.error}</p>}
      {(liveText || text || (defaultStdout && !job)) && (
        <pre className="joblog">{liveText || text || defaultStdout}</pre>
      )}
    </div>
  );
}