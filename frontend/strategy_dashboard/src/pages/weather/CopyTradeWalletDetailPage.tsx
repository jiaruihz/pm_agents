import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { CopyTradePosition, CopyTradeReview, CopyTradeWalletDetail } from "../../data/copy-trade-types";

function fmtUsd(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  const sign = n < 0 ? "-" : "";
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function pct(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function num(value: unknown, digits = 2): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

export function CopyTradeWalletDetailPage(): JSX.Element {
  const { walletAddress = "" } = useParams();
  const [detail, setDetail] = useState<CopyTradeWalletDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    setLoading(true);
    weatherApi
      .getCopyTradeWallet(walletAddress)
      .then((data) => {
        if (dead) return;
        setDetail(data);
        setError("");
      })
      .catch((e: Error) => {
        if (!dead) setError(e.message);
      })
      .finally(() => {
        if (!dead) setLoading(false);
      });
    return () => {
      dead = true;
    };
  }, [walletAddress]);

  const latest = detail?.reviews[0];
  const metrics = latest?.metrics ?? {};
  const displayName = latest?.display_name || `${walletAddress.slice(0, 6)}…${walletAddress.slice(-4)}`;
  const topicRows = useMemo(() => {
    const topics = (metrics.topic_counts ?? {}) as Record<string, number>;
    return Object.entries(topics).sort((a, b) => b[1] - a[1]);
  }, [metrics]);

  return (
    <PageFrame title={displayName} desc="Copy-trade wallet detail · holdings, history, review trail">
      <div className="grid">
        <div>
          <Link to="/copy-trade/wallets" style={{ color: "var(--muted)", textDecoration: "none" }}>
            ← Back to wallets
          </Link>
        </div>

        {error && <section className="card" style={{ color: "var(--bad)" }}>{error}</section>}
        {loading && <section className="card" style={{ color: "var(--muted)" }}>Loading wallet detail…</section>}

        {detail && latest && (
          <>
            <section className="grid cols-4">
              <Kpi label="Verdict" value={latest.verdict.replace("_", " ")} />
              <Kpi label="Score" value={num(latest.score, 1)} />
              <Kpi label="Non-election PnL" value={fmtUsd(metrics.non_election_pnl)} />
              <Kpi label="Profit factor" value={num(metrics.profit_factor, 2)} />
            </section>

            <section className="card">
              <div className="row wrap" style={{ justifyContent: "space-between", marginBottom: 12 }}>
                <div>
                  <h3 style={{ margin: 0 }}>Latest analysis</h3>
                  <div className="mono" style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>
                    {detail.wallet_address}
                  </div>
                </div>
                <div className="row wrap">
                  <Link to={`/copy-trade/capital-efficiency?wallet=${encodeURIComponent(detail.wallet_address)}`}><button>资金效率</button></Link>
                  <span className="badge" style={{ color: verdictColor(latest.verdict), background: `${verdictColor(latest.verdict)}18`, borderColor: `${verdictColor(latest.verdict)}55` }}>
                    {latest.verdict.replace("_", " ")}
                  </span>
                </div>
              </div>

              <div style={metricGridStyle}>
                <Mini label="Closed" value={String(metrics.closed_positions_count ?? "—")} />
                <Mini label="Open" value={String(metrics.open_positions_count ?? detail.open_positions.length)} />
                <Mini label="Sports" value={pct(metrics.sports_ratio)} />
                <Mini label="Arb-like" value={pct(metrics.arbitrage_like_ratio)} />
                <Mini label="Election dep." value={pct(metrics.election_dependency)} />
                <Mini label="Single-event+" value={pct(metrics.positive_single_event_dependency)} />
                <Mini label="Avg entry" value={num(metrics.avg_entry_price, 3)} />
                <Mini label="Diversity" value={pct(metrics.market_diversity_ratio)} />
              </div>

              <div style={{ marginTop: 14, color: "var(--muted)" }}>{latest.summary}</div>
              {latest.reasons.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  {latest.reasons.map((reason) => (
                    <span key={reason} className="badge stale" style={{ marginRight: 6, marginBottom: 6 }}>
                      {reason}
                    </span>
                  ))}
                </div>
              )}
            </section>

            <section className="grid cols-2">
              <section className="card">
                <h3 style={{ marginTop: 0 }}>Topic profile</h3>
                <div className="table-wrap">
                  <table style={{ minWidth: 360 }}>
                    <thead>
                      <tr><th>Topic</th><th>Count</th></tr>
                    </thead>
                    <tbody>
                      {topicRows.map(([topic, count]) => (
                        <tr key={topic}><td>{topic}</td><td className="mono">{count}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="card">
                <h3 style={{ marginTop: 0 }}>Discovery evidence</h3>
                <div className="table-wrap">
                  <table style={{ minWidth: 520 }}>
                    <thead>
                      <tr><th>Method</th><th>Source</th><th>Strength</th></tr>
                    </thead>
                    <tbody>
                      {detail.discoveries.slice(0, 20).map((row, idx) => (
                        <tr key={`${row.method}-${row.source_ref}-${idx}`}>
                          <td>{row.method}</td>
                          <td className="mono">{row.source_ref}</td>
                          <td>{num(row.strength, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </section>

            <PositionTable title="Current positions" rows={detail.open_positions} mode="open" />
            <PositionTable title="Recent closed positions" rows={detail.recent_closed_positions} mode="closed" />

            <section className="card">
              <h3 style={{ marginTop: 0 }}>Review history</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Reviewed</th><th>Verdict</th><th>Score</th><th>Summary</th></tr>
                  </thead>
                  <tbody>
                    {detail.reviews.map((review: CopyTradeReview) => (
                      <tr key={`${review.reviewed_at}-${review.verdict}`}>
                        <td className="mono">{review.reviewed_at}</td>
                        <td>{review.verdict}</td>
                        <td className="mono">{num(review.score, 1)}</td>
                        <td>{review.summary}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </PageFrame>
  );
}

function PositionTable({ title, rows, mode }: { title: string; rows: CopyTradePosition[]; mode: "open" | "closed" }) {
  return (
    <section className="card">
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Market</th>
              <th>Outcome</th>
              <th>Size</th>
              <th>Avg</th>
              <th>{mode === "open" ? "Current" : "PnL"}</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, idx) => (
              <tr key={`${row.condition_id ?? row.slug ?? idx}-${idx}`}>
                <td>
                  <div style={{ fontWeight: 700, maxWidth: 520 }}>{row.title || row.slug || "—"}</div>
                  <div className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>{row.condition_id}</div>
                </td>
                <td>{row.outcome || "—"}</td>
                <td className="mono">{num(row.size, 2)}</td>
                <td className="mono">{num(row.avg_price, 3)}</td>
                <td className="mono">{mode === "open" ? num(row.cur_price, 3) : fmtUsd(row.realized_pnl ?? row.cash_pnl)}</td>
                <td className="mono">{fmtUsd(row.current_value ?? row.initial_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="kpi-value">{value}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="mono" style={{ fontWeight: 700 }}>{value}</div>
      <div style={{ color: "var(--muted)", fontSize: 11 }}>{label}</div>
    </div>
  );
}

function verdictColor(verdict: string): string {
  if (verdict === "paper_candidate") return "var(--ok)";
  if (verdict === "watch") return "var(--warn)";
  if (verdict === "reject") return "var(--bad)";
  return "var(--muted)";
}

const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
  gap: 12,
};
