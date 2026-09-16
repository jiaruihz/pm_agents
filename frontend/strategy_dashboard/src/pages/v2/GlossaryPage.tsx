import { useEffect, useMemo, useState } from "react";
import { weatherApi } from "../../data/weather-http";
import type { GlossaryEntry } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";

export function GlossaryPage() {
  const [fields, setFields] = useState<Record<string, GlossaryEntry> | null>(null);
  const [q, setQ] = useState("");

  useEffect(() => {
    weatherApi.getGlossary().then((r) => setFields(r.fields)).catch(() => setFields({}));
  }, []);

  const entries = useMemo(() => {
    const all = Object.entries(fields ?? {});
    const needle = q.trim().toLowerCase();
    if (!needle) return all;
    return all.filter(([k, v]) => k.toLowerCase().includes(needle) || v.zh.includes(q) || v.definition.includes(q));
  }, [fields, q]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>术语字典</h1>
        <p className="page-sub">canonical 字段 → 中文与口径。全站悬浮提示也用这份字典。</p>
      </header>
      <div className="filter-row">
        <input className="search-input" placeholder="搜字段 / 中文 / 口径…" value={q} onChange={(e) => setQ(e.target.value)} />
      </div>
      {fields == null && <EmptyState message="加载中…" />}
      <table className="data-table">
        <thead><tr><th>字段</th><th>中文</th><th>口径</th><th>来源</th></tr></thead>
        <tbody>
          {entries.map(([k, v]) => (
            <tr key={k}>
              <td><code>{k}</code></td>
              <td>{v.zh}</td>
              <td>{v.definition}</td>
              <td className="muted">{v.source_doc}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
