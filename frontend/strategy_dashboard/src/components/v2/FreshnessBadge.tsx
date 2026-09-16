import { HealthDot } from "./HealthDot";
import { GlossaryTerm } from "./GlossaryTerm";
import type { Freshness } from "../../data/v2-types";

function band(ageMin: number | null, warn: number, bad: number): Freshness {
  if (ageMin == null) return "unknown";
  if (ageMin <= warn) return "fresh";
  if (ageMin <= bad) return "aging";
  return "stale";
}

function ageText(ageMin: number | null): string {
  if (ageMin == null) return "—";
  if (ageMin < 90) return `${Math.round(ageMin)} 分钟前`;
  const h = ageMin / 60;
  return `${h.toFixed(1)} 小时前`;
}

/**
 * Runtime state has separate data and supervisor heartbeat timestamps.  Both are
 * sourced from the current Mac control plane, never from a historical mirror.
 */
export function FreshnessBadge({
  heartbeatAgeMin,
  snapshotAgeMin,
  snapshotTsUtc,
}: {
  heartbeatAgeMin: number | null;
  snapshotAgeMin: number | null;
  snapshotTsUtc: string | null;
}) {
  const snapBand = band(snapshotAgeMin, 20, 120);

  return (
    <div className="freshness-badge">
      <div className="freshness-row">
        <span className="freshness-label">
          <GlossaryTerm field="snapshot_age_min">行情快照</GlossaryTerm>
        </span>
        <HealthDot level={snapBand} label />
        <span className="freshness-meta">
          {ageText(snapshotAgeMin)}
          {snapshotTsUtc && <span className="utc"> · {snapshotTsUtc} UTC</span>}
        </span>
      </div>

      <div className="freshness-row">
        <span className="freshness-label">
          <GlossaryTerm field="heartbeat_age_min">Supervisor 心跳</GlossaryTerm>
        </span>
        <HealthDot level={band(heartbeatAgeMin, 30, 240)} label />
        <span className="freshness-meta">{ageText(heartbeatAgeMin)}</span>
      </div>

      <div className="freshness-row prod">
        <span className="freshness-label">运行判定</span>
        <span className="freshness-note">
          只有 supervisor 心跳在 20 分钟内且进程为 running，才会计入“在跑”。
        </span>
      </div>
    </div>
  );
}
