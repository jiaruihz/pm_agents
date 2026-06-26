import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ResearchLineDetail } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { researchStatusZh } from "./format";

/** Minimal inline markdown: **bold** and `code`. */
function inline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0, m: RegExpExecArray | null, i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) parts.push(<strong key={i++}>{tok.slice(2, -2)}</strong>);
    else parts.push(<code key={i++}>{tok.slice(1, -1)}</code>);
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

/** Render the leading narrative markdown (headings + paragraphs). */
function Narrative({ md }: { md: string }) {
  const blocks = md.split(/\n{2,}/).map((b) => b.trim()).filter(Boolean);
  return (
    <div className="narrative">
      {blocks.map((b, i) => {
        if (b.startsWith("## ")) return <h3 key={i}>{inline(b.slice(3))}</h3>;
        if (b.startsWith("# ")) return <h2 key={i} className="narrative-title">{inline(b.slice(2))}</h2>;
        return <p key={i}>{b.split("\n").map((ln, j) => <span key={j}>{inline(ln)}<br /></span>)}</p>;
      })}
    </div>
  );
}

function isScalar(v: unknown): v is string | number | boolean {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}
function isListOfDicts(v: unknown): v is Record<string, unknown>[] {
  return Array.isArray(v) && v.length > 0 && v.every((x) => x && typeof x === "object" && !Array.isArray(x));
}

function fmtVal(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "number") return Math.abs(v) < 1 && v !== 0 ? v.toFixed(4) : String(v);
  if (typeof v === "boolean") return v ? "是" : "否";
  return String(v);
}

/** Render a list of uniform dicts as a horizontally-scrollable table. */
function DictTable({ rows }: { rows: Record<string, unknown>[] }) {
  const cols = Array.from(new Set(rows.flatMap((r) => Object.keys(r))));
  return (
    <div className="table-scroll">
      <table className="data-table compact">
        <thead><tr>{cols.map((c) => <th key={c}><GlossaryTerm field={c}>{c}</GlossaryTerm></th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{fmtVal(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function KeyVals({ obj }: { obj: Record<string, unknown> }) {
  return (
    <table className="raw-table">
      <tbody>
        {Object.entries(obj).map(([k, v]) => (
          <tr key={k}>
            <td><GlossaryTerm field={k}>{k}</GlossaryTerm></td>
            <td>{isScalar(v) ? fmtVal(v) : <span className="muted">{Array.isArray(v) ? `${v.length} 项` : "见原始 JSON"}</span>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ResearchLineDetailPage() {
  const { lineId = "" } = useParams();
  const [data, setData] = useState<ResearchLineDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [raw, setRaw] = useState(false);

  useEffect(() => {
    weatherApi.getResearchLine(lineId).then(setData).catch((e) => setError(String(e)));
  }, [lineId]);

  if (error) return <div className="page"><div className="error-banner">{error}</div></div>;
  if (!data) return <div className="page"><EmptyState message="加载中…" /></div>;

  const s = data.summary;
  const verdict = (s.verdict && typeof s.verdict === "object" ? s.verdict : null) as Record<string, unknown> | null;
  const scalars = Object.entries(s).filter(([, v]) => isScalar(v));
  const sections = Object.entries(s).filter(([k, v]) => k !== "verdict" && !isScalar(v));

  return (
    <div className="page">
      <header className="page-head">
        <Link to="/research" className="back-link">← 研究证据</Link>
        <h1>{String(s.title ?? s.strategy ?? data.line_id)}</h1>
        <p className="page-sub">{data.summary_path}</p>
      </header>

      {data.narrative_md && (
        <section className="card">
          <h2>策略思路 · 为什么这么做</h2>
          <Narrative md={data.narrative_md} />
          {data.doc_path && <p className="muted" style={{ fontSize: 12, marginTop: 8 }}>完整分析：{data.doc_path}</p>}
        </section>
      )}

      {verdict && (
        <section className="card verdict-card" data-ready={String(verdict.live_ready)}>
          <div className="verdict-head">
            <span className="badge" data-tone={verdict.live_ready ? "fresh" : "stale"}>
              {verdict.live_ready ? "够 live" : "未达 live"}
            </span>
            <strong title={String(verdict.status ?? "")}>{researchStatusZh(String(verdict.status ?? ""))}</strong>
          </div>
          {verdict.reason != null && <p className="verdict-reason">{String(verdict.reason)}</p>}
        </section>
      )}

      {scalars.length > 0 && (
        <section className="card">
          <h2>概览</h2>
          <div className="meta-grid">
            {scalars.map(([k, v]) => (
              <div key={k} className="meta-item">
                <span className="meta-key"><GlossaryTerm field={k}>{k}</GlossaryTerm></span>
                <span className="meta-val">{fmtVal(v)}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {sections.map(([k, v]) => (
        <section key={k} className="card">
          <h2><GlossaryTerm field={k}>{k}</GlossaryTerm></h2>
          {isListOfDicts(v) ? <DictTable rows={v} /> : v && typeof v === "object" ? <KeyVals obj={v as Record<string, unknown>} /> : <div className="muted">{fmtVal(v)}</div>}
        </section>
      ))}

      <section className="card">
        <button className="raw-toggle" onClick={() => setRaw((x) => !x)}>{raw ? "收起原始 JSON ▲" : "看原始 JSON ▼"}</button>
        {raw && <pre className="json-block">{JSON.stringify(s, null, 2)}</pre>}
      </section>
    </div>
  );
}
