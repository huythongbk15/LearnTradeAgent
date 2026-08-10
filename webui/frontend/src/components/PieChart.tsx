const PALETTE = ["#4cc9f0", "#f72585", "#ffd166", "#06d6a0", "#b5179e", "#ff9e00", "#3a86ff", "#8338ec", "#00bbf9", "#ef476f"];

function arcPath(cx: number, cy: number, r: number, start: number, end: number) {
  const large = end - start > Math.PI ? 1 : 0;
  return [
    `M${cx},${cy}`,
    `L${cx + r * Math.cos(start)},${cy + r * Math.sin(start)}`,
    `A${r},${r} 0 ${large} 1 ${cx + r * Math.cos(end)},${cy + r * Math.sin(end)}`,
    "Z",
  ].join(" ");
}

/** Donut chart SVG thuần (không cần thư viện). */
export function PieChart({
  labels,
  values,
  size = 260,
}: {
  labels: string[];
  values: number[];
  size?: number;
}) {
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 30;
  let acc = -Math.PI / 2;
  const arcs = values.map((v, i) => {
    const start = acc;
    const end = acc + (v / total) * Math.PI * 2;
    acc = end;
    return { path: arcPath(cx, cy, r, start, end), color: PALETTE[i % PALETTE.length], label: labels[i], value: v };
  });
  const pct = (v: number) => ((v / total) * 100).toFixed(1) + "%";

  return (
    <div className="row" style={{ gap: 18, alignItems: "center", flexWrap: "wrap" }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {arcs.map((a, i) => (
          <path key={i} d={a.path} fill={a.color} stroke="#0b1c29" strokeWidth="2">
            <title>{`${a.label}: ${pct(a.value)}`}</title>
          </path>
        ))}
        <circle cx={cx} cy={cy} r={r * 0.62} fill="var(--bg)" />
        <text x={cx} y={cy - 4} textAnchor="middle" fill="var(--text)" fontSize={18} fontWeight={700}>
          {total > 0 ? "100%" : "—"}
        </text>
        <text x={cx} y={cy + 16} textAnchor="middle" fill="var(--muted)" fontSize={11}>
          tổng weights
        </text>
      </svg>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 180 }}>
        {arcs.map((a, i) => (
          <div key={i} className="row" style={{ gap: 8, alignItems: "center" }}>
            <span style={{ width: 12, height: 12, borderRadius: 3, background: a.color, display: "inline-block" }} />
            <span className="mono" style={{ flex: 1 }}>{a.label}</span>
            <b>{pct(a.value)}</b>
          </div>
        ))}
      </div>
    </div>
  );
}