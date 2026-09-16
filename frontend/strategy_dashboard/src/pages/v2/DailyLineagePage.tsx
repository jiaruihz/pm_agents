import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { LiveBookRow } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { usd } from "./format";

const STAGES = ["signal 信号", "plan 计划", "order 下单", "fill 成交", "settlement 结算"];

/** Shorten the executor strategy_name (drops the _notional_$.._shares_.. suffix). */
function shortStrategy(name: string | null): string {
  if (!name) return "—";
  return name.replace(/^t1_trading_/, "").replace(/_notional_.*$/, "");
}

export function DailyLineagePage() {
  const { date } = useParams();
  const navigate = useNavigate();
  const [allDates, setAllDates] = useState<string[]>([]);
  const [rows, setRows] = useState<LiveBookRow[] | null>(null);

  // load the full book once to populate the date picker
  useEffect(() => {
    weatherApi.getLiveBook({ status: "all", limit: 2000 }).then((r) => {
      const ds = Array.from(new Set(r.rows.map((x) => x.target_date).filter(Boolean) as string[])).sort().reverse();
      setAllDates(ds);
    }).catch(() => setAllDates([]));
  }, []);

  // load rows for the chosen date
  useEffect(() => {
    if (!date) { setRows(null); return; }
    weatherApi.getLiveBook({ target_date: date, status: "all", limit: 500 }).then((r) => setRows(r.rows)).catch(() => setRows([]));
  }, [date]);

  const byCity = useMemo(() => {
    const m = new Map<string, LiveBookRow[]>();
    (rows ?? []).forEach((r) => {
      const k = r.city ?? "—";
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(r);
    });
    return Array.from(m.entries());
  }, [rows]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>单日血缘</h1>
        <p className="page-sub">某日 live 实盘逐笔：signal → plan → order → fill → settlement。按城市归并。</p>
        <div className="stage-flow">{STAGES.map((s) => <span key={s} className="stage-chip">{s}</span>)}</div>
      </header>

      <div className="filter-row">
        日期：
        <select value={date ?? ""} onChange={(e) => navigate(`/lineage/${e.target.value}`)}>
          <option value="" disabled>选择日期</option>
          {allDates.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      {!date && <EmptyState message="选择一个日期查看逐笔血缘" hint={allDates.length ? `最近有数据的日期：${allDates[0]}` : undefined} />}
      {date && rows != null && rows.length === 0 && <EmptyState message={`${date} 没有 live 实盘记录`} />}

      {date && byCity.map(([city, crows]) => (
        <section key={city} className="card">
          <h2>{city} <span className="muted">({crows.length} 笔)</span></h2>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th><GlossaryTerm field="bracket">档</GlossaryTerm></th>
                  <th><GlossaryTerm field="side">方向</GlossaryTerm></th>
                  <th><GlossaryTerm field="edge">edge</GlossaryTerm></th>
                  <th><GlossaryTerm field="market_price">市场价</GlossaryTerm></th>
                  <th><GlossaryTerm field="fill_price">成交价</GlossaryTerm></th>
                  <th><GlossaryTerm field="cost_usd">成本</GlossaryTerm></th>
                  <th><GlossaryTerm field="forecast_source">数据源</GlossaryTerm></th>
                  <th><GlossaryTerm field="settlement_status">结算</GlossaryTerm></th>
                  <th>盈亏</th>
                </tr>
              </thead>
              <tbody>
                {crows.map((r) => (
                  <tr key={r.fill_id}>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 11 }} title={r.strategy_name ?? ""}>{shortStrategy(r.strategy_name)}</td>
                    <td>{r.bracket ?? "—"}</td>
                    <td>{r.side?.includes("NO") ? "NO" : "YES"}</td>
                    <td>{r.edge == null ? "—" : r.edge.toFixed(3)}</td>
                    <td>{r.market_price ?? "—"}</td>
                    <td>{r.fill_price ?? "—"}</td>
                    <td>{usd(r.cost_usd)}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{(r.forecast_source ?? "").replace("open_meteo_live_", "") || "—"}</td>
                    <td>{r.settled ? "已结算" : <span className="muted">未结算</span>}</td>
                    <td style={{ color: ((r.settled ? r.pnl_usd_at_fill : r.unrealized_pnl_mid) ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
                      {r.settled ? usd(r.pnl_usd_at_fill, true) : usd(r.unrealized_pnl_mid, true)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
