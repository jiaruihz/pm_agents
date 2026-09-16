export function Sparkline({
  points,
  color = "var(--accent-2)",
}: {
  points: number[];
  color?: string;
}): JSX.Element {
  if (!points.length) {
    return <div className="mono">No history</div>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const path = points
    .map((v, i) => {
      const x = (i / Math.max(points.length - 1, 1)) * 100;
      const y = 100 - ((v - min) / span) * 100;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg className="spark" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="sparkline">
      <path d={path} stroke={color} strokeWidth="2.5" fill="none" />
    </svg>
  );
}
