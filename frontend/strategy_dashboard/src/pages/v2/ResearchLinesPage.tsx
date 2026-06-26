import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ResearchLineRow } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { pct, researchStatusZh } from "./format";

function rocRoi(l: ResearchLineRow): number | null {
  return l.forward_roi ?? l.holdout_roi ?? l.repr_roi;
}

export function ResearchLinesPage() {
  const [lines, setLines] = useState<ResearchLineRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [onlyGate, setOnlyGate] = useState(false);

  useEffect(() => {
    weatherApi.getResearchLines().then((r) => setLines(r.lines)).catch((e) => setError(String(e)));
  }, []);

  const rows = useMemo(() => {
    const ls = (lines ?? []).slice().sort((a, b) => (b.generated_at_utc ?? "").localeCompare(a.generated_at_utc ?? ""));
    return onlyGate ? ls.filter((l) => l.gate_ready) : ls;
  }, [lines, onlyGate]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>研究证据</h1>
        <p className="page-sub">各策略线的前向证据登记册。CI 跨 0 即不是可 live 规则，只是 shadow 候选。点开看变体对比与完整结论。</p>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {lines == null && !error && <EmptyState message="加载中…" />}
      {lines != null && lines.length === 0 && <EmptyState message="没有研究线" hint="docs/analysis/**/generated/*/summary.json 为空。" />}

      {lines != null && lines.length > 0 && (
        <>
          <label className="filter-row">
            <input type="checkbox" checked={onlyGate} onChange={(e) => setOnlyGate(e.target.checked)} /> 只看够 live-gate 的
          </label>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>研究线</th><th>状态</th><th>代表 ROI</th><th>CI</th>
                  <th>变体</th><th>够 live?</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((l) => (
                  <tr key={l.line_id}>
                    <td>
                      <Link to={`/research/${encodeURIComponent(l.line_id)}`}>{l.title}</Link>
                      {l.verdict_reason && <div className="row-note">{l.verdict_reason}</div>}
                    </td>
                    <td title={l.status}>{researchStatusZh(l.status)}</td>
                    <td>{pct(rocRoi(l))}</td>
                    <td>
                      {l.ci_low == null ? "—" : `[${pct(l.ci_low)}, ${pct(l.ci_high)}]`}
                      {l.ci_crosses_zero && <span className="badge" data-tone="aging" style={{ marginLeft: 6 }}>跨 0</span>}
                    </td>
                    <td>{l.variant_count || "—"}</td>
                    <td>{l.gate_ready ? <span className="badge" data-tone="fresh">够</span> : <span className="badge" data-tone="stale">不够</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
