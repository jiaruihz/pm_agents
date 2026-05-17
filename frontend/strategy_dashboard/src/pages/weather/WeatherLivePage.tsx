/**
 * WeatherLivePage — Real-time monitor for paper and live runs.
 * Polls the API every 10 seconds for active runs and their recent fills.
 * Route: /weather/live
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { RunSummary, TradeRow } from "../../data/weather-types";

const POLL_INTERVAL_MS = 10_000;
const RECENT_TRADE_LIMIT = 30;

interface RunWithTrades {
  run: RunSummary;
  trades: TradeRow[];
  lastUpdated: Date;
}

function fmtPnl(val: string | null): string {
  if (!val) return "—";
  const n = parseFloat(val);
  return `${n >= 0 ? "+" : ""}$${n.toFixed(2)}`;
}

function pnlColor(val: string | null): string {
  if (!val) return "var(--muted)";
  const n = parseFloat(val);
  return n > 0 ? "var(--ok)" : n < 0 ? "var(--bad)" : "inherit";
}

export function WeatherLivePage() {
  const [activeRuns, setActiveRuns] = useState<RunWithTrades[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPoll, setLastPoll] = useState<Date | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function pollOnce() {
    try {
      // Fetch paper + live runs
      const [paperRuns, liveRuns] = await Promise.all([
        weatherApi.listRuns({ state: "paper", limit: 10 }),
        weatherApi.listRuns({ state: "live", limit: 10 }),
      ]);
      const runs = [...liveRuns, ...paperRuns]; // live first

      // For each run, fetch recent fills
      const withTrades = await Promise.all(
        runs.map(async (run): Promise<RunWithTrades> => {
          const trades = await weatherApi.getRunTrades(run.run_id, { limit: RECENT_TRADE_LIMIT }).catch(() => []);
          return { run, trades, lastUpdated: new Date() };
        }),
      );

      setActiveRuns(withTrades);
      setLastPoll(new Date());
      setError(null);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    pollOnce();
    timerRef.current = setInterval(pollOnce, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  return (
    <PageFrame title="Live Monitor" desc="Paper & Live run activity · 实时监控">
      <>
        {/* Status bar */}
        <div style={{
          display: "flex", gap: 16, alignItems: "center",
          marginBottom: 20, fontSize: 12, color: "var(--muted)",
        }}>
          <div>
            Polling every {POLL_INTERVAL_MS / 1000}s
            <span style={{
              marginLeft: 8,
              display: "inline-block",
              width: 8, height: 8, borderRadius: "50%",
              background: error ? "var(--bad)" : "var(--ok)",
              verticalAlign: "middle",
            }} />
          </div>
          {lastPoll && (
            <div>Last update: {lastPoll.toLocaleTimeString()}</div>
          )}
          {error && <div style={{ color: "var(--bad)" }}>{error}</div>}
          {loading && !lastPoll && <div>Loading…</div>}
        </div>

        {/* No active runs */}
        {!loading && activeRuns.length === 0 && (
          <div style={{ color: "var(--muted)", textAlign: "center", padding: 48, fontSize: 14 }}>
            No paper or live runs found · 暂无纸盘/实盘
            <br />
            <Link to="/weather/runs" style={{ color: "var(--accent-2)", marginTop: 8, display: "inline-block" }}>
              → View all runs
            </Link>
          </div>
        )}

        {/* Run panels */}
        <div style={{ display: "flex", flexDirection: "column", gap: 24 }}>
          {activeRuns.map(({ run, trades }) => (
            <RunPanel key={run.run_id} run={run} trades={trades} />
          ))}
        </div>
      </>
    </PageFrame>
  );
}

function RunPanel({ run, trades }: { run: RunSummary; trades: TradeRow[] }) {
  const settledTrades = trades.filter((t) => t.final_yes !== null && t.final_yes !== undefined);
  const totalPnl = settledTrades.reduce((acc, t) => acc + (t.pnl_usd ? parseFloat(t.pnl_usd) : 0), 0);
  const winners = settledTrades.filter((t) => t.pnl_usd && parseFloat(t.pnl_usd) > 0).length;
  const winRate = settledTrades.length > 0 ? winners / settledTrades.length : null;

  return (
    <div style={{
      background: "var(--card)", border: "1px solid var(--stroke)",
      borderRadius: 12, overflow: "hidden",
    }}>
      {/* Run header */}
      <div style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--stroke)",
        display: "flex", gap: 16, alignItems: "center", flexWrap: "wrap",
      }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--muted)" }}>Run ID</div>
          <div style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, fontWeight: 600 }}>
            {run.run_id.slice(0, 16)}…
          </div>
        </div>
        <StatChip label="State" value={run.state} accent={run.state === "live"} />
        <StatChip label="Mode" value={run.execution_mode} />
        {run.date_range_start && (
          <StatChip label="Range" value={`${run.date_range_start}${run.date_range_end ? ` → ${run.date_range_end}` : " →"}`} />
        )}
        {settledTrades.length > 0 && (
          <>
            <StatChip
              label="PnL (settled)"
              value={`${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`}
              color={totalPnl > 0 ? "var(--ok)" : totalPnl < 0 ? "var(--bad)" : undefined}
            />
            {winRate !== null && (
              <StatChip
                label="Win Rate"
                value={`${(winRate * 100).toFixed(0)}%`}
                color={winRate > 0.5 ? "var(--ok)" : winRate < 0.4 ? "var(--bad)" : undefined}
              />
            )}
          </>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <Link
            to={`/weather/history/${run.run_id}`}
            style={{ fontSize: 11, color: "var(--accent-2)" }}
          >
            Full history →
          </Link>
        </div>
      </div>

      {/* Recent trades */}
      {trades.length === 0 ? (
        <div style={{ padding: 20, color: "var(--muted)", fontSize: 12, textAlign: "center" }}>
          No trades yet
        </div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
            <thead>
              <tr>
                {["Date", "City", "Bracket", "Side", "Shares", "Cost", "Settlement", "PnL"].map((h) => (
                  <th key={h} style={{
                    textAlign: "left", padding: "7px 12px",
                    borderBottom: "1px solid var(--stroke)",
                    color: "var(--muted)", fontWeight: 600,
                  }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.slice(0, 20).map((t, i) => (
                <tr key={`${t.signal_id}-${i}`} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={td}>{t.target_date ?? "—"}</td>
                  <td style={td}>{t.city ?? "—"}</td>
                  <td style={{ ...td, fontFamily: "IBM Plex Mono, monospace" }}>{t.bracket ?? "—"}</td>
                  <td style={td}>
                    <span style={{
                      color: t.order_side === "BUY_YES" ? "var(--ok)" : "var(--accent)",
                      fontWeight: 600,
                    }}>
                      {t.order_side ?? t.signal_side ?? "—"}
                    </span>
                  </td>
                  <td style={{ ...td, fontFamily: "IBM Plex Mono, monospace" }}>{t.shares ?? "—"}</td>
                  <td style={{ ...td, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.cost_usd ? `$${parseFloat(t.cost_usd).toFixed(2)}` : "—"}
                  </td>
                  <td style={td}>
                    {t.final_yes !== null && t.final_yes !== undefined ? (
                      <span style={{ color: t.final_yes === 1 ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>
                        {t.final_yes === 1 ? "YES" : "NO"}
                      </span>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>{t.settlement_status ?? "pending"}</span>
                    )}
                  </td>
                  <td style={{ ...td, fontFamily: "IBM Plex Mono, monospace", color: pnlColor(t.pnl_usd), fontWeight: 600 }}>
                    {fmtPnl(t.pnl_usd)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {trades.length > 20 && (
            <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--muted)" }}>
              Showing 20 of {trades.length} recent trades ·{" "}
              <Link to={`/weather/history/${run.run_id}`} style={{ color: "var(--accent-2)" }}>
                View all
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function StatChip({ label, value, accent, color }: {
  label: string; value: string; accent?: boolean; color?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--muted)" }}>{label}</div>
      <div style={{
        fontWeight: 600, fontSize: 12,
        color: color ?? (accent ? "var(--accent)" : "inherit"),
      }}>{value}</div>
    </div>
  );
}

const td: React.CSSProperties = { padding: "6px 12px", verticalAlign: "middle" };
