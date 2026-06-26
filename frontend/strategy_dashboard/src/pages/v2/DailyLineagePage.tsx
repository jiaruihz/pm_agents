import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { LivePosition } from "../../data/weather-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { usd } from "./format";

const STAGES = ["signal 信号", "plan 计划", "order 下单", "fill 成交", "settlement 结算"];

export function DailyLineagePage() {
  const { date } = useParams();
  const navigate = useNavigate();
  const [positions, setPositions] = useState<LivePosition[] | null>(null);

  useEffect(() => {
    weatherApi.getLivePositions({ limit: 500 }).then(setPositions).catch(() => setPositions([]));
  }, []);

  const dates = useMemo(() => {
    const s = new Set((positions ?? []).map((p) => p.target_date).filter(Boolean) as string[]);
    return Array.from(s).sort().reverse();
  }, [positions]);

  const rows = (positions ?? []).filter((p) => p.target_date === date);

  return (
    <div className="page">
      <header className="page-head">
        <h1>单日血缘</h1>
        <p className="page-sub">signal → plan → order → fill → settlement 逐笔复盘。</p>
        <div className="stage-flow">
          {STAGES.map((s) => <span key={s} className="stage-chip">{s}</span>)}
        </div>
      </header>

      <div className="filter-row">
        日期：
        <select value={date ?? ""} onChange={(e) => navigate(`/lineage/${e.target.value}`)}>
          <option value="" disabled>选择日期</option>
          {dates.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>

      {!date && <EmptyState message="选择一个日期查看逐笔血缘" />}
      {date && positions != null && rows.length === 0 && <EmptyState message={`${date} 没有 real fill 记录`} />}

      {date && rows.length > 0 && (
        <table className="data-table">
          <thead>
            <tr><th>城市</th><th>档位</th><th>方向</th><th>signal edge</th><th>成交价</th><th>成交成本</th><th>结算</th><th>已实现</th></tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.fill_id}>
                <td>{p.city ?? "—"}</td>
                <td>{p.bracket ?? "—"}</td>
                <td>{p.order_side}</td>
                <td>{p.signal_edge == null ? "—" : p.signal_edge.toFixed(3)}</td>
                <td>{p.filled_price == null ? "—" : p.filled_price.toFixed(3)}</td>
                <td>{usd(p.cost_usd)}</td>
                <td>{p.settlement_status ?? "—"}</td>
                <td>{p.settlement_status === "settled" ? usd(p.pnl_usd, true) : <span className="muted">未结算</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
