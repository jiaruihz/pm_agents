import type { RunMetrics } from "../../data/weather-types";
import { METRIC_LABELS } from "../../data/weather-types";

// Grouped display: primary KPIs first, then risk, then details
const DISPLAY_ORDER: (keyof RunMetrics)[] = [
  // Coverage
  "num_trades", "settled_trades", "unsettled_trades", "settled_ratio",
  // Core PnL
  "total_pnl_usd", "roi", "win_rate", "loss_rate",
  // Per-trade stats
  "avg_pnl_usd", "median_pnl_usd", "pnl_trimmed_1pct",
  "avg_win_usd", "avg_loss_usd", "expectancy_usd",
  // Risk
  "worst_loss_usd", "best_win_usd", "max_drawdown_usd",
  // Concentration
  "top1_pnl_share", "top5_pnl_share",
  // Cost
  "total_cost_usd", "fees_paid_usd",
];

const RATE_KEYS = new Set<keyof RunMetrics>([
  "win_rate", "loss_rate", "roi", "settled_ratio",
  "top1_pnl_share", "top5_pnl_share",
]);
const MONEY_KEYS = new Set<keyof RunMetrics>([
  "total_pnl_usd", "avg_pnl_usd", "median_pnl_usd", "pnl_trimmed_1pct",
  "total_cost_usd", "fees_paid_usd", "worst_loss_usd", "best_win_usd",
  "expectancy_usd", "max_drawdown_usd", "avg_win_usd", "avg_loss_usd",
]);

function fmt(key: keyof RunMetrics, val: number | null): string {
  if (val === null || val === undefined) return "—";
  if (RATE_KEYS.has(key)) return `${(val * 100).toFixed(1)}%`;
  if (MONEY_KEYS.has(key)) {
    const sign = val >= 0 ? "+" : "-";
    return `${sign}$${Math.abs(val).toFixed(2)}`;
  }
  return String(val);
}

const POS_KEYS = new Set<keyof RunMetrics>(["total_pnl_usd", "avg_pnl_usd", "median_pnl_usd",
  "pnl_trimmed_1pct", "expectancy_usd", "avg_win_usd", "best_win_usd"]);
const NEG_KEYS = new Set<keyof RunMetrics>(["worst_loss_usd", "avg_loss_usd", "max_drawdown_usd"]);

function pnlColor(key: keyof RunMetrics, val: number | null): string {
  if (val === null) return "";
  if (POS_KEYS.has(key)) return val > 0 ? "var(--ok)" : val < 0 ? "var(--bad)" : "";
  if (NEG_KEYS.has(key)) return val < 0 ? "var(--bad)" : "";
  if (key === "win_rate") return val >= 0.5 ? "var(--ok)" : val < 0.4 ? "var(--bad)" : "";
  if (key === "roi") return val > 0 ? "var(--ok)" : val < 0 ? "var(--bad)" : "";
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
