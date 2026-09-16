import type { Freshness } from "../../data/v2-types";

const COLOR: Record<Freshness, string> = {
  fresh: "var(--ok)",
  aging: "var(--warn)",
  stale: "var(--bad)",
  unknown: "var(--muted)",
};

const LABEL: Record<Freshness, string> = {
  fresh: "新鲜",
  aging: "偏旧",
  stale: "陈旧",
  unknown: "无数据",
};

export function HealthDot({ level, label }: { level: Freshness; label?: boolean }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" }}>
      <span
        aria-label={LABEL[level]}
        style={{
          width: 9,
          height: 9,
          borderRadius: 99,
          background: COLOR[level],
          boxShadow: `0 0 0 3px ${COLOR[level]}22`,
          flex: "0 0 auto",
        }}
      />
      {label && <span style={{ color: COLOR[level], fontWeight: 600 }}>{LABEL[level]}</span>}
    </span>
  );
}

/** Map common lifecycle_status strings to a freshness band for the status badge. */
export function statusToFreshness(status: string | null): Freshness {
  switch (status) {
    case "live":
    case "shadow":
    case "telemetry":
    case "monitor":
      return "fresh";
    case "stale":
      return "aging";
    case "blocked":
    case "shelved":
      return "stale";
    default:
      return "unknown";
  }
}
