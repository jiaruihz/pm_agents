/**
 * WeatherTradeDrilldownPage
 *
 * Vertical blood lineage for one trade:
 *   Signal → Plan → Order (CLOB + Paper) → Fill → Settlement → PnL
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { TradeDrilldown } from "../../data/weather-types";

// ─── helpers ────────────────────────────────────────────────────────────────

function fmt(v: unknown, fallback = "—"): string {
  if (v === null || v === undefined || v === "") return fallback;
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}

function fmtPrice(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}¢` : "—";
}

function fmtMoney(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(4)}` : "—";
}

function fmtPct(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—";
}

function computePnL(
  order: Record<string, unknown> | undefined,
  fill: Record<string, unknown> | undefined,
  settlement: Record<string, unknown> | null | undefined,
): { pnl: number | null; label: string } {
  if (!fill || !settlement) return { pnl: null, label: "Unsettled" };
  const shares = Number(fill["filled_shares"]);
  const fillPrice = Number(fill["filled_price"]);
  const finalPrice = Number(settlement["final_price"]);
  const fees = Number(fill["fees_usd"] ?? 0) || 0;
  const side = String(order?.["order_side"] ?? "");
  if (!Number.isFinite(shares) || !Number.isFinite(fillPrice) || !Number.isFinite(finalPrice)) {
    return { pnl: null, label: "Missing data" };
  }
  const payout = side === "BUY_YES" ? shares * finalPrice : shares * (1 - finalPrice);
  const cost = shares * fillPrice;
  const pnl = payout - cost - fees;
  return { pnl, label: fmtMoney(pnl) };
}

// ─── sub-components ─────────────────────────────────────────────────────────

function ChainArrow() {
  return (
    <div style={{ display: "flex", justifyContent: "center", padding: "4px 0", color: "var(--muted)", fontSize: 18 }}>
      ↓
    </div>
  );
}

function StepCard({
  title,
  status,
  statusColor,
  children,
}: {
  title: string;
  status?: string;
  statusColor?: string;
  children: React.ReactNode;
}) {
  return (
    <div style={cardStyle}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 13, color: "var(--accent-2)" }}>{title}</span>
        {status && (
          <span style={{
            fontSize: 11, padding: "2px 7px", borderRadius: 6,
            background: `${statusColor ?? "var(--muted)"}22`,
            color: statusColor ?? "var(--muted)", fontWeight: 600,
          }}>
            {status}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function KVGrid({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: "8px 16px" }}>
      {items.map(([label, value]) => (
        <div key={label}>
          <div style={{ color: "var(--muted)", fontSize: 11, marginBottom: 2 }}>{label}</div>
          <div style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 12, overflowWrap: "anywhere" }}>
            {value ?? "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

function JsonToggle({ label, value }: { label: string; value: unknown }) {
  if (!value) return null;
  return (
    <details style={{ marginTop: 8 }}>
      <summary style={{ cursor: "pointer", color: "var(--accent-2)", fontSize: 11 }}>{label}</summary>
      <pre style={{ fontSize: 11, marginTop: 4, overflowX: "auto", color: "var(--muted)" }}>
        {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
      </pre>
    </details>
  );
}

// ─── main page ───────────────────────────────────────────────────────────────

export function WeatherTradeDrilldownPage() {
  const { runId, signalId } = useParams<{ runId: string; signalId: string }>();
  const [data, setData] = useState<TradeDrilldown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!runId || !signalId) return;
    setLoading(true);
    setError(null);
    weatherApi.getTradeDrilldown(runId, signalId)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId, signalId]);

  const sig = data?.signal ?? null;
  const plan = data?.plans?.[0] ?? null;

  // Separate CLOB vs paper orders
  const clobOrders = (data?.orders ?? []).filter(o => o["venue"] === "polymarket_clob");
  const paperOrders = (data?.orders ?? []).filter(o => o["venue"] === "paper");
  const clobOrder = clobOrders[0];
  const paperOrder = paperOrders[0];

  // Prefer real CLOB fill over paper simulated fill for PnL
  const clobFill  = (data?.fills ?? []).find(f => f["status"] === "filled");
  const paperFill = (data?.fills ?? []).find(f => f["status"] === "simulated");
  const primaryFill = clobFill ?? paperFill;
  const primaryOrder = clobFill ? clobOrder : paperOrder;

  const settlement = data?.settlement ?? null;
  const { pnl, label: pnlLabel } = computePnL(primaryOrder, primaryFill, settlement);
  const pnlColor = pnl == null ? "var(--muted)" : pnl >= 0 ? "var(--ok)" : "var(--bad)";

  // CLOB order status
  const clobExResponse = clobOrder?.["exchange_response"] as Record<string, unknown> | undefined;
  const clobPlaceInfo = clobExResponse?.["place"] as Record<string, unknown> | undefined;
  const clobOrderId = clobPlaceInfo?.["orderID"] as string | undefined
    ?? clobOrder?.["order_id"] as string | undefined;
  const clobStatus = String(clobOrder?.["status"] ?? "");
  const clobStatusColor =
    clobStatus === "filled"    ? "var(--ok)" :
    clobStatus === "error"     ? "var(--bad)" :
    clobStatus === "submitted" ? "var(--accent-2)" : "var(--muted)";

  return (
    <PageFrame
      title="Trade Drilldown"
      desc={`Signal → Plan → Order → Fill → Settlement · ${signalId?.slice(0, 16) ?? ""}…`}
    >
      <>
        {/* Nav */}
        <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "center" }}>
          <Link to={`/weather/history/${runId}`} style={linkStyle}>← Back to history</Link>
          {loading && <span style={{ color: "var(--muted)", fontSize: 13 }}>Loading…</span>}
        </div>
        {error && <div style={{ color: "var(--bad)", marginBottom: 12 }}>{error}</div>}

        {data && (
          <>
            {/* ── PnL banner ─────────────────────────────────────────────── */}
            <div style={{
              ...cardStyle,
              marginBottom: 16,
              background: pnl != null ? `${pnlColor}11` : "var(--card)",
              border: `1px solid ${pnl != null ? pnlColor : "var(--stroke)"}44`,
            }}>
              <div style={{ display: "flex", gap: 32, flexWrap: "wrap", alignItems: "center" }}>
                <div>
                  <div style={{ color: "var(--muted)", fontSize: 11 }}>Trade PnL</div>
                  <div style={{ fontSize: 28, fontWeight: 700, color: pnlColor, fontFamily: "IBM Plex Mono, monospace" }}>
                    {pnlLabel}
                  </div>
                </div>
                <KVGrid items={[
                  ["City", fmt(sig?.["city"])],
                  ["Date", fmt(sig?.["target_date"])],
                  ["Bracket", fmt(sig?.["bracket"])],
                  ["Side", fmt(primaryOrder?.["order_side"])],
                  ["Fill Price", fmtPrice(primaryFill?.["filled_price"])],
                  ["Final Price", fmtPrice(settlement?.["final_price"])],
                  ["Shares", fmt(primaryFill?.["filled_shares"])],
                  ["Cost", fmtMoney(
                    Number(primaryFill?.["filled_shares"]) * Number(primaryFill?.["filled_price"])
                  )],
                ]} />
              </div>
            </div>

            {/* ── ① Signal ───────────────────────────────────────────────── */}
            <StepCard
              title="① Signal"
              status={fmt(sig?.["signal_side"])}
              statusColor="var(--accent-2)"
            >
              <KVGrid items={[
                ["signal_id", <span style={{ fontSize: 10 }}>{fmt(sig?.["signal_id"]).slice(0, 20)}…</span>],
                ["city / pool", `${fmt(sig?.["city"])} · ${fmt(sig?.["city_pool"])}`],
                ["target_date", fmt(sig?.["target_date"])],
                ["bracket", fmt(sig?.["bracket"])],
                ["model", fmt(sig?.["model_version"])],
                ["forecast_source", fmt(sig?.["forecast_source"])],
                ["model_p_yes", fmtPct(sig?.["model_p_yes"])],
                ["market_price", fmtPrice(sig?.["market_price"])],
                ["edge", fmtPct(sig?.["edge"])],
                ["hours_to_settle", fmt(sig?.["hours_to_settle"])],
                ["condition_id", <span style={{ fontSize: 10 }}>{fmt(sig?.["condition_id"]).slice(0, 22)}…</span>],
                ["snapshot_ts", fmt(sig?.["snapshot_ts_utc"])],
              ]} />
            </StepCard>

            <ChainArrow />

            {/* ── ② Plan ─────────────────────────────────────────────────── */}
            <StepCard
              title="② Plan"
              status={plan
                ? (fmt(plan["skip_reason"]) === "—" ? "accepted" : `skipped: ${fmt(plan["skip_reason"])}`)
                : "no plan"}
              statusColor={plan && fmt(plan["skip_reason"]) === "—" ? "var(--ok)" : "var(--bad)"}
            >
              {plan ? (
                <KVGrid items={[
                  ["execution_policy", fmt(plan["execution_policy"])],
                  ["sizing_mode", fmt(plan["sizing_mode"])],
                  ["notional", fmtMoney(plan["notional"])],
                  ["desired_shares", fmt(plan["desired_shares"])],
                  ["limit_price", fmtPrice(plan["limit_price"])],
                  ["entry_price_window", fmt(plan["entry_price_window"])],
                  ["skip_reason", fmt(plan["skip_reason"])],
                  ["created_at", fmt(plan["created_at_utc"])],
                ]} />
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>Signal had no plan generated.</div>
              )}
            </StepCard>

            <ChainArrow />

            {/* ── ③ CLOB Order ───────────────────────────────────────────── */}
            <StepCard
              title="③ CLOB Order (Live)"
              status={clobOrder ? clobStatus : "not placed"}
              statusColor={clobOrder ? clobStatusColor : "var(--muted)"}
            >
              {clobOrder ? (
                <>
                  <KVGrid items={[
                    ["orderID", clobOrderId
                      ? <span style={{ fontSize: 10 }}>{clobOrderId.slice(0, 26)}…</span>
                      : "—"],
                    ["limit_price", fmtPrice(clobOrder["limit_price"])],
                    ["entry_price", fmtPrice(clobOrder["entry_price"])],
                    ["shares", fmt(clobOrder["shares"])],
                    ["cost_usd", fmtMoney(clobOrder["cost_usd"])],
                    ["best_bid / ask", `${fmtPrice(clobExResponse?.["best_bid"])} / ${fmtPrice(clobExResponse?.["best_ask"])}`],
                    ["execution_id", <span style={{ fontSize: 10 }}>{fmt(clobOrder["execution_id"]).slice(0, 20)}…</span>],
                    ["placed_at", fmt(clobOrder["placed_at_utc"] ?? clobOrder["created_at_utc"])],
                  ]} />
                  {clobStatus === "submitted" && (
                    <div style={{ marginTop: 8, fontSize: 12, color: "var(--accent-2)" }}>
                      ⏳ Maker order is live in the book — fill status pending CLOB sync
                    </div>
                  )}
                  {clobStatus === "error" && (
                    <div style={{ marginTop: 8, fontSize: 12, color: "var(--bad)" }}>
                      ✗ CLOB placement failed
                    </div>
                  )}
                  <JsonToggle label="exchange_response JSON" value={clobOrder["exchange_response"]} />
                </>
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>No CLOB order placed for this signal.</div>
              )}
            </StepCard>

            <ChainArrow />

            {/* ── ④ Paper Fill ───────────────────────────────────────────── */}
            <StepCard
              title="④ Paper Fill (Shadow Track)"
              status={paperFill ? "simulated" : "none"}
              statusColor={paperFill ? "var(--accent-2)" : "var(--muted)"}
            >
              {paperFill ? (
                <KVGrid items={[
                  ["filled_shares", fmt(paperFill["filled_shares"])],
                  ["filled_price", fmtPrice(paperFill["filled_price"])],
                  ["cost", fmtMoney(Number(paperFill["filled_shares"]) * Number(paperFill["filled_price"]))],
                  ["fees_usd", fmtMoney(paperFill["fees_usd"])],
                  ["filled_at", fmt(paperFill["filled_at_utc"] ?? paperFill["created_at_utc"])],
                ]} />
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>
                  {clobFill ? "No paper fill — CLOB fill used for PnL." : "No fill recorded yet."}
                </div>
              )}
            </StepCard>

            {/* ── ④b CLOB Fill (if synced) ───────────────────────────────── */}
            {clobFill && (
              <>
                <ChainArrow />
                <StepCard title="④b CLOB Fill (Real)" status="filled" statusColor="var(--ok)">
                  <KVGrid items={[
                    ["filled_shares", fmt(clobFill["filled_shares"])],
                    ["filled_price", fmtPrice(clobFill["filled_price"])],
                    ["cost", fmtMoney(Number(clobFill["filled_shares"]) * Number(clobFill["filled_price"]))],
                    ["fees_usd", fmtMoney(clobFill["fees_usd"])],
                    ["filled_at", fmt(clobFill["filled_at_utc"])],
                  ]} />
                </StepCard>
              </>
            )}

            <ChainArrow />

            {/* ── ⑤ Settlement ───────────────────────────────────────────── */}
            <StepCard
              title="⑤ Settlement"
              status={settlement ? fmt(settlement["settlement_status"]) : "pending"}
              statusColor={
                !settlement ? "var(--muted)" :
                settlement["settlement_status"] === "settled" ? "var(--ok)" : "var(--bad)"
              }
            >
              {settlement ? (
                <KVGrid items={[
                  ["target_date", fmt(settlement["target_date"])],
                  ["bracket", fmt(settlement["bracket"])],
                  ["final_price", fmtPrice(settlement["final_price"])],
                  ["result", Number(settlement["final_price"]) >= 0.5 ? "YES wins ✓" : "NO wins ✓"],
                  ["settlement_status", fmt(settlement["settlement_status"])],
                  ["condition_id", <span style={{ fontSize: 10 }}>{fmt(settlement["condition_id"]).slice(0, 22)}…</span>],
                ]} />
              ) : (
                <div style={{ color: "var(--muted)", fontSize: 13 }}>
                  Market not yet settled. Target: <strong>{fmt(sig?.["target_date"])}</strong>
                </div>
              )}
            </StepCard>

            {/* ── Artifacts ─────────────────────────────────────────────── */}
            {data.artifacts && data.artifacts.length > 0 && (
              <>
                <ChainArrow />
                <StepCard title="Run Artifacts">
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {data.artifacts.map((a, i) => (
                      <div key={i} style={{ fontSize: 12, display: "flex", gap: 8 }}>
                        <span style={{ color: "var(--accent-2)", minWidth: 90 }}>{fmt(a["artifact_kind"])}</span>
                        <span style={{ color: "var(--muted)" }}>{fmt(a["source_path"])}</span>
                        {a["row_count"] != null && <span>({fmt(a["row_count"])} rows)</span>}
                      </div>
                    ))}
                  </div>
                </StepCard>
              </>
            )}
          </>
        )}
      </>
    </PageFrame>
  );
}

// ─── styles ─────────────────────────────────────────────────────────────────

const cardStyle: React.CSSProperties = {
  background: "var(--card)",
  border: "1px solid var(--stroke)",
  borderRadius: 10,
  padding: "14px 16px",
  marginBottom: 4,
};

const linkStyle: React.CSSProperties = {
  color: "var(--accent-2)",
  textDecoration: "none",
  fontSize: 13,
};
