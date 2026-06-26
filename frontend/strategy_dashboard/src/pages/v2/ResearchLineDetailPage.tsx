import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ResearchLineDetail } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";

export function ResearchLineDetailPage() {
  const { lineId = "" } = useParams();
  const [data, setData] = useState<ResearchLineDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getResearchLine(lineId).then(setData).catch((e) => setError(String(e)));
  }, [lineId]);

  if (error) return <div className="page"><div className="error-banner">{error}</div></div>;
  if (!data) return <div className="page"><EmptyState message="加载中…" /></div>;

  return (
    <div className="page">
      <header className="page-head">
        <Link to="/research" className="back-link">← 研究证据</Link>
        <h1>{String(data.summary.title ?? data.line_id)}</h1>
        <p className="page-sub">{data.summary_path}</p>
      </header>
      <section className="card">
        <h2>summary.json</h2>
        <pre className="json-block">{JSON.stringify(data.summary, null, 2)}</pre>
      </section>
    </div>
  );
}
