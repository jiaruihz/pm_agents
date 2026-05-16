/**
 * WeatherComparePage — Select multiple runs and compare metrics side by side.
 * run_ids is synced to URL: /weather/compare?run_ids=id1,id2,id3
 */
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import { MetricsCard } from "./MetricsCard";
import type { CompareRun, RunSummary } from "../../data/weather-types";

export function WeatherComparePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawIds = searchParams.get("run_ids") ?? "";
  const selectedIds = rawIds ? rawIds.split(",").filter(Boolean) : [];

  const [inputVal, setInputVal] = useState(rawIds);
  const [compareData, setCompareData] = useState<CompareRun[]>([]);
  const [allRuns, setAllRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load run list for the dropdown picker
  useEffect(() => {
    weatherApi.listRuns({ limit: 200 }).then(setAllRuns).catch(() => {});
  }, []);

  // Fetch comparison when selectedIds changes
  useEffect(() => {
    if (selectedIds.length === 0) {
      setCompareData([]);
      return;
    }
    setLoading(true);
    setError(null);
    weatherApi
      .compareRuns(selectedIds)
      .then((r) => setCompareData(r.runs))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [rawIds]);

  function applyIds(ids: string) {
    setInputVal(ids);
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (ids) next.set("run_ids", ids);
      else next.delete("run_ids");
      return next;
    });
  }

  function toggleRun(runId: string) {
    const current = new Set(selectedIds);
    if (current.has(runId)) current.delete(runId);
    else current.add(runId);
    const next = Array.from(current).join(",");
    applyIds(next);
  }

  return (
    <PageFrame title="Compare Runs" desc="Side-by-side metrics comparison · 运行对比">
      <>
        <div style={{ display: "flex", gap: 16, marginBottom: 24, flexWrap: "wrap", alignItems: "flex-start" }}>
          {/* Manual ID input */}
          <div style={{ flex: "0 0 360px" }}>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>
              Paste run IDs (comma-separated)
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                value={inputVal}
                onChange={(e) => setInputVal(e.target.value)}
                placeholder="run_id1, run_id2, ..."
                style={{
                  flex: 1, padding: "7px 12px", borderRadius: 8,
                  border: "1px solid var(--stroke)", background: "var(--card)",
                  fontSize: 12, fontFamily: "IBM Plex Mono, monospace",
                }}
              />
              <button
                onClick={() => applyIds(inputVal)}
                style={{
                  padding: "7px 14px", borderRadius: 8, border: "none",
                  background: "var(--accent-2)", color: "#fff",
                  cursor: "pointer", fontSize: 12,
                }}
              >
                Compare
              </button>
            </div>
          </div>

          {/* Run picker */}
          <div style={{ flex: "1 1 300px" }}>
            <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 4 }}>
              Click to select/deselect runs
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 120, overflowY: "auto" }}>
              {allRuns.map((r) => {
                const active = selectedIds.includes(r.run_id);
                return (
                  <button
                    key={r.run_id}
                    onClick={() => toggleRun(r.run_id)}
                    style={{
                      padding: "4px 10px", borderRadius: 6, border: "1px solid",
                      borderColor: active ? "var(--accent-2)" : "var(--stroke)",
                      background: active ? "rgba(42,95,255,0.12)" : "var(--card)",
                      color: active ? "var(--accent-2)" : "var(--ink)",
                      cursor: "pointer", fontSize: 11,
                      fontFamily: "IBM Plex Mono, monospace",
                    }}
                    title={r.run_id}
                  >
                    {r.run_id.slice(0, 10)}… <span style={{ opacity: 0.6 }}>{r.state}</span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {error && <div style={{ color: "var(--bad)", marginBottom: 16 }}>{error}</div>}
        {loading && <div style={{ color: "var(--muted)" }}>Loading…</div>}

        {/* Side-by-side metrics */}
        {compareData.length > 0 && (
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            {compareData.map((run) => (
              <div key={run.run_id} style={{ flex: "1 1 260px", minWidth: 220, maxWidth: 340 }}>
                <div style={{
                  fontSize: 11, fontFamily: "IBM Plex Mono, monospace",
                  color: "var(--muted)", marginBottom: 6,
                }}>
                  {run.run_id.slice(0, 16)}…
                </div>
                <div style={{ fontSize: 12, marginBottom: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
                  {run.execution_mode && <Tag>{run.execution_mode}</Tag>}
                  {run.state && <Tag accent>{run.state}</Tag>}
                  {run.tags?.map((t) => <Tag key={t}>{t}</Tag>)}
                </div>
                {run.error ? (
                  <div style={{
                    background: "var(--card)", border: "1px solid var(--stroke)",
                    borderRadius: 12, padding: 20, color: "var(--bad)",
                  }}>
                    {run.error}
                  </div>
                ) : run.metrics ? (
                  <MetricsCard
                    metrics={run.metrics}
                    title={run.date_range_start
                      ? `${run.date_range_start}${run.date_range_end ? ` → ${run.date_range_end}` : ""}`
                      : undefined}
                  />
                ) : (
                  <div style={{ color: "var(--muted)", fontSize: 13 }}>No metrics</div>
                )}
              </div>
            ))}
          </div>
        )}

        {!loading && selectedIds.length === 0 && (
          <div style={{ color: "var(--muted)", fontSize: 14, textAlign: "center", padding: 48 }}>
            Select runs to compare · 选择运行进行对比
          </div>
        )}
      </>
    </PageFrame>
  );
}

function Tag({ children, accent }: { children: React.ReactNode; accent?: boolean }) {
  return (
    <span style={{
      display: "inline-block", padding: "2px 7px", borderRadius: 6,
      background: accent ? "rgba(255,106,61,0.1)" : "rgba(42,95,255,0.1)",
      color: accent ? "var(--accent)" : "var(--accent-2)",
      fontSize: 11,
    }}>
      {children}
    </span>
  );
}
