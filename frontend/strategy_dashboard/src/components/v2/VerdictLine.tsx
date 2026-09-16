/** A plain-language one-line verdict shown above the raw fields. */
export function VerdictLine({ tone = "neutral", children }: {
  tone?: "good" | "warn" | "bad" | "neutral";
  children: React.ReactNode;
}) {
  const color =
    tone === "good" ? "var(--ok)" : tone === "warn" ? "var(--warn)" : tone === "bad" ? "var(--bad)" : "var(--ink)";
  return (
    <p className="verdict-line" style={{ borderLeft: `3px solid ${color}` }}>
      {children}
    </p>
  );
}
