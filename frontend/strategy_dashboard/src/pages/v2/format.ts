export function pct(n: number | null | undefined, digits = 1): string {
  if (n == null) return "—";
  const v = (n * 100).toFixed(digits);
  return `${n > 0 ? "+" : ""}${v}%`;
}

export function usd(n: number | null | undefined, sign = false): string {
  if (n == null) return "—";
  const prefix = n < 0 ? "-" : sign && n > 0 ? "+" : "";
  return `${prefix}$${Math.abs(n).toFixed(2)}`;
}

export function num(n: number | null | undefined): string {
  return n == null ? "—" : String(n);
}

const LIFECYCLE_ZH: Record<string, string> = {
  live: "实盘",
  shadow: "影子",
  telemetry: "遥测",
  monitor: "监视",
  paper: "纸面",
  research: "研究",
  blocked: "受阻",
  stale: "陈旧",
  shelved: "停用",
};

export function lifecycleZh(s: string | null | undefined): string {
  if (!s) return "—";
  return LIFECYCLE_ZH[s] ?? s;
}

const AUDIT_ZH: Record<string, string> = {
  obs_not_ok: "观测不可用",
  observation_cache_missing: "观测缓存缺失",
  stale_obs: "观测过旧",
  snapshot_metar_missing: "快照缺 METAR",
  station_map_missing: "站点映射缺失",
  no_current_local_date_market: "无当日市场",
  no_market: "无盘口",
  outside_decision_hours: "不在决策时段",
  no_current_bracket: "无当前档位",
  no_order_placed: "未下单",
  insufficient_edge: "edge 不足",
  cap_reached: "已达仓位上限",
};

export function auditZh(s: string | null | undefined): string {
  if (!s) return "—";
  return AUDIT_ZH[s] ?? s;
}
