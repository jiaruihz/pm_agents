export type MonitorSourceStatus = "fresh" | "stale" | "unavailable";
export type BusinessStatus = "observed" | "unknown" | "stale" | "not_applicable";

export interface RuntimeMonitorMetric {
  key: string;
  label: string;
  value: number | string | null;
  unit: string;
  scope: string;
}

export interface RuntimeMonitorModule {
  id: string;
  label: string;
  status: string;
  observed_at_utc: string | null;
  detail: string;
  metrics: RuntimeMonitorMetric[];
}

export interface RuntimeMonitorRuntime {
  instance_id: string;
  role: string;
  execution_mode: string;
  desired_state: string;
  present: boolean | null;
  status: string;
  health_age_sec: number | null;
  health_budget_sec: number | null;
  dependencies: string[];
  issues: string[];
  diagnosis: { code: string; label: string; detail: string };
  business: {
    status: BusinessStatus;
    observed_at_utc: string | null;
    scope: string;
    source_ref: string | null;
    metrics: RuntimeMonitorMetric[];
    blockers: string[];
  };
}

export interface RuntimeMonitorResponse {
  schema_version: "platform_monitor_v1";
  observed_at_utc: string;
  source_observed_at_utc: string | null;
  source_age_sec: number | null;
  source_status: MonitorSourceStatus;
  source_error: string | null;
  refresh_budget_sec: number;
  summary: {
    total: number;
    running: number;
    paused: number;
    critical: number;
    unknown: number;
    business_observed: number;
  };
  modules: RuntimeMonitorModule[];
  runtimes: RuntimeMonitorRuntime[];
}
