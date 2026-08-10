/** Thanh tiến độ job: % + stage text. */
export function ProgressBar({ progress }: { progress?: { pct: number; stage: string } | null }) {
  if (!progress) return null;
  const pct = Math.max(0, Math.min(100, progress.pct));
  return (
    <div style={{ margin: "8px 0" }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 3 }}>
        <span style={{ color: "var(--muted)" }}>{progress.stage}</span>
        <b style={{ color: "var(--accent)" }}>{pct}%</b>
      </div>
      <div
        style={{
          height: 8,
          background: "var(--panel)",
          borderRadius: 5,
          overflow: "hidden",
          border: "1px solid var(--line)",
        }}
      >
        <div
          style={{
            width: `${pct}%`,
            height: "100%",
            background: pct >= 100 ? "var(--green)" : "var(--accent)",
            transition: "width 0.4s ease",
            borderRadius: 4,
          }}
        />
      </div>
    </div>
  );
}