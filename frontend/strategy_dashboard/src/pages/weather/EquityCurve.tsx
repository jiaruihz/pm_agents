/**
 * EquityCurve — lightweight SVG line chart for cumulative PnL over time.
 * No external chart library needed.
 */
interface Point {
  date: string;
  cumulative_pnl: number;
}

interface Props {
  points: Point[];
}

const W = 800;
const H = 180;
const PAD = { top: 16, right: 16, bottom: 32, left: 64 };

function lerp(value: number, fromMin: number, fromMax: number, toMin: number, toMax: number): number {
  if (fromMax === fromMin) return (toMin + toMax) / 2;
  return toMin + ((value - fromMin) / (fromMax - fromMin)) * (toMax - toMin);
}

export function EquityCurve({ points }: Props) {
  if (points.length < 2) return null;

  const pnls = points.map((p) => p.cumulative_pnl);
  const minPnl = Math.min(0, ...pnls);
  const maxPnl = Math.max(0, ...pnls);
  const finalPnl = pnls[pnls.length - 1];
  const isPositive = finalPnl >= 0;

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  // Map point index → x, pnl → y
  const xs = points.map((_, i) => PAD.left + lerp(i, 0, points.length - 1, 0, innerW));
  const ys = points.map((p) => PAD.top + lerp(p.cumulative_pnl, maxPnl, minPnl, 0, innerH));

  // Zero line y
  const zeroY = PAD.top + lerp(0, maxPnl, minPnl, 0, innerH);

  // Build SVG path
  const lineD = points.map((_, i) => `${i === 0 ? "M" : "L"} ${xs[i].toFixed(1)} ${ys[i].toFixed(1)}`).join(" ");

  // Fill polygon: line + down to zero line + back
  const fillD = [
    lineD,
    `L ${xs[xs.length - 1].toFixed(1)} ${zeroY.toFixed(1)}`,
    `L ${xs[0].toFixed(1)} ${zeroY.toFixed(1)}`,
    "Z",
  ].join(" ");

  // Y-axis ticks (3 labels: max, zero, min)
  const yTicks = [
    { val: maxPnl, y: PAD.top },
    { val: 0, y: zeroY },
    { val: minPnl, y: PAD.top + innerH },
  ].filter((t, i, arr) => i === 0 || Math.abs(t.y - arr[i - 1].y) > 16);

  // X-axis ticks (first, middle, last)
  const xTickIdxs = [0, Math.floor((points.length - 1) / 2), points.length - 1];

  const strokeColor = isPositive ? "var(--ok)" : "var(--bad)";
  const fillColor = isPositive ? "rgba(0,200,80,0.10)" : "rgba(255,80,60,0.10)";

  return (
    <div style={{
      background: "var(--card)",
      border: "1px solid var(--stroke)",
      borderRadius: 12,
      padding: "12px 16px",
    }}>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 6 }}>
        Equity Curve · 盈亏曲线
        <span style={{
          marginLeft: 12, fontFamily: "IBM Plex Mono, monospace",
          fontWeight: 600, color: strokeColor,
        }}>
          {finalPnl >= 0 ? "+" : ""}${finalPnl.toFixed(2)}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", height: H, display: "block", overflow: "visible" }}
        preserveAspectRatio="none"
      >
        {/* Zero line */}
        <line
          x1={PAD.left} y1={zeroY} x2={W - PAD.right} y2={zeroY}
          stroke="var(--stroke)" strokeWidth={1} strokeDasharray="4 3"
        />

        {/* Fill area */}
        <path d={fillD} fill={fillColor} />

        {/* Line */}
        <path d={lineD} fill="none" stroke={strokeColor} strokeWidth={2} strokeLinejoin="round" />

        {/* Y-axis ticks */}
        {yTicks.map((t) => (
          <g key={t.val}>
            <line x1={PAD.left - 4} y1={t.y} x2={PAD.left} y2={t.y} stroke="var(--stroke)" strokeWidth={1} />
            <text
              x={PAD.left - 8} y={t.y + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--muted)"
              fontFamily="IBM Plex Mono, monospace"
            >
              {t.val >= 0 ? "+" : ""}${t.val.toFixed(0)}
            </text>
          </g>
        ))}

        {/* X-axis labels */}
        {xTickIdxs.map((idx) => (
          <text
            key={idx}
            x={xs[idx]}
            y={H - 4}
            textAnchor={idx === 0 ? "start" : idx === points.length - 1 ? "end" : "middle"}
            fontSize={10}
            fill="var(--muted)"
          >
            {points[idx].date}
          </text>
        ))}
      </svg>
    </div>
  );
}
