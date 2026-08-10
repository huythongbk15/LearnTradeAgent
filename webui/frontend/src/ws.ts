import { useEffect, useState } from "react";
import type { Snapshot } from "./api";

const WS_URL =
  (import.meta.env.VITE_WS_URL as string | undefined) ??
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;

export function useRealtime(onSnapshot?: (s: Snapshot) => void) {
  const [connected, setConnected] = useState(false);
  const [last, setLast] = useState<Snapshot | null>(null);
  const [history, setHistory] = useState<{ ts: number; equity: number }[]>([]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(WS_URL);
      ws.onopen = () => {
        setConnected(true);
        retry = 0;
      };
      ws.onmessage = (ev) => {
        try {
          const snap = JSON.parse(ev.data) as Snapshot;
          setLast(snap);
          setHistory((h) => {
            const next = [...h, { ts: snap.ts, equity: snap.portfolio.equity }];
            return next.length > 400 ? next.slice(next.length - 400) : next;
          });
          onSnapshot?.(snap);
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) {
          retry += 1;
          setTimeout(connect, Math.min(1000 * retry, 8000));
        }
      };
      ws.onerror = () => ws?.close();
    };
    connect();
    return () => {
      closed = true;
      ws?.close();
    };
  }, [onSnapshot]);

  return { connected, last, history };
}