import { useEffect, useRef } from "react";
import { createChart, ColorType, type IChartApi } from "lightweight-charts";

interface Props {
  data: { ts: number; equity: number }[];
}

export function EquityChart({ data }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      height: 240,
      layout: {
        background: { type: ColorType.Solid, color: "#12161f" },
        textColor: "#8b94a7",
      },
      grid: {
        vertLines: { color: "#1a2030" },
        horzLines: { color: "#1a2030" },
      },
      rightPriceScale: { borderColor: "#232a3b" },
      timeScale: { borderColor: "#232a3b" },
    });
    chartRef.current = chart;
    const series = chart.addLineSeries({
      color: "#4da3ff",
      lineWidth: 2,
      priceLineVisible: false,
    });
    series.setData(data.map((d) => ({ time: d.ts as never, value: d.equity })));
    chart.timeScale().fitContent();
    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || data.length === 0) return;
    const series = chart.addLineSeries({ color: "#4da3ff", lineWidth: 2, priceLineVisible: false });
    // series API: replace data — remove & re-add to keep it simple on updates
    chart.removeSeries(series);
    const s2 = chart.addLineSeries({ color: "#4da3ff", lineWidth: 2, priceLineVisible: false });
    s2.setData(data.map((d) => ({ time: d.ts as never, value: d.equity })));
    chart.timeScale().fitContent();
  }, [data]);

  return <div ref={ref} />;
}