import type { RunMetrics } from "../../data/weather-types";
import { METRIC_LABELS } from "../../data/weather-types";

// Which metrics to show and their display format
const DISPLAY_ORDER: (keyof RunMetrics)[] = [
  "num_trades",
  "settled_trades",
  "unsettled_trades",
  "total_pnl_usd",
  "win_rate",
  "avg_pnl_usd",
  "median_pnl_usd",
  "pnl_trimmed_1pct",
  "total_cost_usd",
  "roi",
  "top1_pnl_share",
  "top5_pnl_share",
];

function fmt(key: keyof RunMetrics, val: number | null): string {
  if (val === null || val === undefined) return "—";
  if (key === "win_rate" || key === "roi" || key.endsWith("_share"))
    return `${(val * 100).toFixed(1)}%`;
  if (key.endsWith("_usd") || key === "total_cost_usd")
    return `$${val >= 0 ? "+" : ""}${val.toFixed(2)}`;
  return String(val);
}

function pnlColor(key: keyof RunMetrics, val: number | null): string {
  if (val === null) return "";
  if (key === "total_pnl_usd" || key === "avg_pnl_usd" || key === "median_pnl_usd" || key === "pnl_trimmed_1pct") {
    return val > 0 ? "var(--ok)" : val < 0 ? "var(--bad)" : "";
  }
  if (key === "win_rate" || key === "roi") {
    return val > 0.5 ? "var(--ok)" : val < 0.4 ? "var(--bad)" : "";
  }
  return "";
}

interface Props {
  metrics: RunMetrics;
  title?: string;
}

export function MetricsCard({ metrics, title }: Props) {
  return (
    <div style={{
      background: "var(--card)",
      border: "1px solid var(--stroke)",
      borderRadius: 12,
      padding: "16px 20px",
      minWidth: 220,
    }}>
      {title && (
        <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 13 }}>{title}</div>
      )}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <tbody>
          {DISPLAY_ORDER.map((key) => {
            const val = metrics[key] as number | null;
            const labels = METRIC_LABELS[key];
            const color = pnlColor(key, val);
            return (
              <tr key={key} style={{ borderBottom: "1px solid var(--stroke)" }}>
                <td style={{ padding: "5px 0", color: "var(--muted)" }}>
                  {labels.en}
                  <span style={{ marginLeft: 6, fontSize: 11, opacity: 0.6 }}>{labels.zh}</span>
                </td>
                <td style={{
                  padding: "5px 0",
                  textAlign: "right",
                  fontFamily: "IBM Plex Mono, monospace",
                  fontWeight: 600,
                  color: color || "inherit",
                }}>
                  {fmt(key, val)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
