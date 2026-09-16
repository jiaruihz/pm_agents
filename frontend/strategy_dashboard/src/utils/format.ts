export function fmtNum(value: unknown, digits = 4): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "-";
}

export function fmtInt(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? String(Math.trunc(n)) : "-";
}

export function fmtDate(value: unknown): string {
  if (!value) return "-";
  const d = new Date(String(value));
  return Number.isFinite(d.getTime()) ? d.toLocaleString() : String(value);
}

export function fmtPct(value: unknown, digits = 1): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "-";
}

export function fmtCurrency(value: unknown): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(n);
}

export function fmtAge(seconds: unknown): string {
  const n = Number(seconds);
  if (!Number.isFinite(n) || n < 0) return "-";
  if (n < 60) return `${Math.floor(n)}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m ${Math.floor(n % 60)}s`;
  if (n < 86400) return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
  return `${Math.floor(n / 86400)}d ${Math.floor((n % 86400) / 3600)}h`;
}
