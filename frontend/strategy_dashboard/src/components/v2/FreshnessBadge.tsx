import { useState } from "react";
import { HealthDot } from "./HealthDot";
import { GlossaryTerm } from "./GlossaryTerm";
import type { Freshness } from "../../data/v2-types";

const DOCTOR_CMD =
  "ssh jiarui@192.168.0.200 'cd ~/projects/weather-predict && scripts/ops/doctor_restart.sh'";

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
 * Two distinct freshness layers, never conflated:
 *  1) mirror sync pulse (heartbeat_age_min / snapshot_age_min) — local mirror lag
 *  2) production liveness — NOT inferred from mirror age; links to N100 doctor.
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
  const [copied, setCopied] = useState(false);
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
          <GlossaryTerm field="heartbeat_age_min">镜像心跳</GlossaryTerm>
        </span>
        <HealthDot level={band(heartbeatAgeMin, 30, 240)} label />
        <span className="freshness-meta">{ageText(heartbeatAgeMin)}</span>
      </div>

      <div className="freshness-row prod">
        <span className="freshness-label">生产是否断流</span>
        <span className="freshness-note">
          镜像旧 ≠ 生产死。判断断流请跑 N100 doctor：
          <button
            className="copy-cmd"
            onClick={() => {
              navigator.clipboard?.writeText(DOCTOR_CMD);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            title={DOCTOR_CMD}
          >
            {copied ? "已复制 ✓" : "复制 doctor 命令"}
          </button>
        </span>
      </div>
    </div>
  );
}
