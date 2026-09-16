import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { CopyTradeSummary, CopyTradeWallet } from "../../data/copy-trade-types";

const VERDICTS = ["all", "paper_candidate", "watch", "reject"] as const;

function fmtUsd(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

function pct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

function shortWallet(wallet: string): string {
  return `${wallet.slice(0, 6)}…${wallet.slice(-4)}`;
}

function isCoreCandidate(wallet: CopyTradeWallet): boolean {
  return (
    wallet.verdict === "paper_candidate" &&
    wallet.profit_factor >= 1.8 &&
    wallet.sports_ratio < 0.2 &&
    wallet.election_dependency < 0.25 &&
    wallet.positive_single_event_dependency < 0.35
  );
}

export function CopyTradeWalletsPage(): JSX.Element {
  const [summary, setSummary] = useState<CopyTradeSummary | null>(null);
  const [wallets, setWallets] = useState<CopyTradeWallet[]>([]);
  const [verdict, setVerdict] = useState<(typeof VERDICTS)[number]>("all");
  const [error, setError] = useState<string>("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    setLoading(true);
    Promise.all([
      weatherApi.getCopyTradeSummary(),
      weatherApi.listCopyTradeWallets({
        verdict: verdict === "all" ? undefined : verdict,
        scan_mode: "full_scan",
        limit: 200,
      }),
    ])
      .then(([nextSummary, walletList]) => {
        if (dead) return;
        setSummary(nextSummary);
        setWallets(walletList.items);
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
  }, [verdict]);

  const core = useMemo(() => wallets.filter(isCoreCandidate), [wallets]);

  return (
    <PageFrame
      title="Copy Trade Wallets"
      desc="Wallet-centric smart-money research · full-scan candidates and rejection reasons"
    >
      <div className="grid">
        {error && <section className="card" style={{ color: "var(--bad)" }}>{error}</section>}

        <section className="grid cols-4">
          <Kpi label="Discovered wallets" value={summary ? String(summary.wallets_count) : "—"} />
          <Kpi label="Discovery evidence" value={summary ? String(summary.discoveries_count) : "—"} />
          <Kpi label="Full-scan paper" value={summary ? String(summary.full_scan_counts.paper_candidate ?? 0) : "—"} />
          <Kpi label="Core candidates" value={summary ? String(summary.core_candidates) : "—"} />
        </section>

        <section className="card">
          <div className="row wrap" style={{ justifyContent: "space-between", marginBottom: 12 }}>
            <div>
              <h3 style={{ margin: 0 }}>Full-scan wallet review</h3>
              <div style={{ color: "var(--muted)", fontSize: 13, marginTop: 4 }}>
                Strict filter: non-election edge, low sports/arb, low one-event dependency.
              </div>
            </div>
            <div className="row wrap">
              {VERDICTS.map((v) => (
                <button
                  key={v}
                  className={verdict === v ? "primary" : ""}
                  onClick={() => setVerdict(v)}
                  style={{ padding: "7px 10px" }}
                >
                  {v.replace("_", " ")}
                </button>
              ))}
            </div>
          </div>

          {loading && <div style={{ color: "var(--muted)", padding: 20 }}>Loading wallets…</div>}

          {!loading && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Wallet</th>
                    <th>Verdict</th>
                    <th>Score</th>
                    <th>Non-election PnL</th>
                    <th>PF</th>
                    <th>Sports</th>
                    <th>Election</th>
                    <th>Single event</th>
                    <th>Closed / Open</th>
                    <th>Primary topics</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {wallets.map((wallet) => (
                    <WalletRow key={`${wallet.wallet_address}-${wallet.reviewed_at}`} wallet={wallet} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {core.length > 0 && (
          <section className="card">
            <h3 style={{ marginTop: 0 }}>Core watchlist</h3>
            <div className="grid cols-2">
              {core.map((wallet) => (
                <div key={wallet.wallet_address} style={watchCardStyle}>
                  <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
                    <div>
                      <div style={{ fontWeight: 700 }}>{wallet.display_name || shortWallet(wallet.wallet_address)}</div>
                      <div className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>{wallet.wallet_address}</div>
                    </div>
                    <VerdictBadge verdict={wallet.verdict} />
                  </div>
                  <div style={watchMetricGridStyle}>
                    <MiniMetric label="Non-election" value={fmtUsd(wallet.non_election_pnl)} />
                    <MiniMetric label="PF" value={wallet.profit_factor.toFixed(2)} />
                    <MiniMetric label="Election" value={pct(wallet.election_dependency)} />
                    <MiniMetric label="Open" value={String(wallet.open_positions_count)} />
                  </div>
                  <div style={{ color: "var(--muted)", fontSize: 12 }}>
                    {topTopics(wallet.topic_counts).join(" · ")}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </PageFrame>
  );
}

function WalletRow({ wallet }: { wallet: CopyTradeWallet }) {
  const reasons = wallet.reasons.length ? wallet.reasons.join(", ") : wallet.history_truncated ? "history truncated" : "passed filters";
  return (
    <tr>
      <td>
        <Link
          to={`/copy-trade/wallets/${encodeURIComponent(wallet.wallet_address)}`}
          style={{ color: "inherit", textDecoration: "none" }}
        >
          <div style={{ fontWeight: 700 }}>{wallet.display_name || shortWallet(wallet.wallet_address)}</div>
        </Link>
        <div className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>{wallet.wallet_address}</div>
      </td>
      <td><VerdictBadge verdict={wallet.verdict} /></td>
      <td className="mono">{wallet.score.toFixed(1)}</td>
      <td className="mono">{fmtUsd(wallet.non_election_pnl)}</td>
      <td className="mono">{wallet.profit_factor >= 100 ? "999+" : wallet.profit_factor.toFixed(2)}</td>
      <td>{pct(wallet.sports_ratio)}</td>
      <td>{pct(wallet.election_dependency)}</td>
      <td>{pct(wallet.positive_single_event_dependency)}</td>
      <td className="mono">{wallet.closed_positions_count} / {wallet.open_positions_count}</td>
      <td>{topTopics(wallet.topic_counts).join(", ")}</td>
      <td style={{ maxWidth: 280, color: "var(--muted)" }}>{reasons}</td>
    </tr>
  );
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const color =
    verdict === "paper_candidate" ? "var(--ok)" :
    verdict === "watch" ? "var(--warn)" :
    verdict === "reject" ? "var(--bad)" : "var(--muted)";
  return (
    <span className="badge" style={{ color, borderColor: `${color}55`, background: `${color}18` }}>
      {verdict.replace("_", " ")}
    </span>
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

function MiniMetric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="mono" style={{ fontWeight: 700 }}>{value}</div>
      <div style={{ color: "var(--muted)", fontSize: 11 }}>{label}</div>
    </div>
  );
}

function topTopics(topics: Record<string, number>): string[] {
  return Object.entries(topics)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([topic, count]) => `${topic}:${count}`);
}

const watchCardStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 10,
  padding: 14,
  background: "rgba(255,255,255,0.48)",
  display: "grid",
  gap: 12,
};

const watchMetricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
  gap: 10,
};
