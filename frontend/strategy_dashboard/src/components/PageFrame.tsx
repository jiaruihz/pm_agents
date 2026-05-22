import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";

function useClock() {
  const [now, setNow] = useState(() => new Date().toLocaleString());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date().toLocaleString()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

// ---- Weather (new canonical system) ----
const WEATHER_NAV = [
  { to: "/weather/strategies", label: "📊 Strategies", sub: "策略列表" },
  { to: "/weather/runs",       label: "⛅ Runs",        sub: "运行记录" },
  { to: "/weather/live",       label: "🔴 Live",        sub: "实盘监控" },
  { to: "/weather/compare",    label: "⚖ Compare",     sub: "对比分析" },
];

// ---- Legacy (old strategy_runtime.db, deprecated) ----
const LEGACY_NAV = [
  { to: "/dashboard",  label: "总览" },
  { to: "/strategies", label: "策略" },
  { to: "/accounts",   label: "账户" },
  { to: "/research",   label: "Research" },
  { to: "/backtests",  label: "Backtests" },
];

function tabFromPath(pathname: string): "weather" | "legacy" {
  return pathname.startsWith("/weather") ? "weather" : "legacy";
}

export function PageFrame({ title, desc, children }: {
  title: string;
  desc: string;
  children: JSX.Element;
}): JSX.Element {
  const clock = useClock();
  const { pathname } = useLocation();
  const [tab, setTab] = useState<"weather" | "legacy">(tabFromPath(pathname));

  // Sync tab if URL changes externally
  useEffect(() => {
    setTab(tabFromPath(pathname));
  }, [pathname]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        {/* Brand */}
        <div className="brand">STRAT OPS</div>
        <div className="brand-sub">Strategy Runtime Console</div>

        {/* Tab switcher */}
        <div style={{
          display: "flex", gap: 0,
          margin: "12px 8px 14px",
          border: "1px solid var(--stroke)",
          borderRadius: 8, overflow: "hidden",
        }}>
          <TabButton label="Weather" active={tab === "weather"} onClick={() => setTab("weather")} />
          <TabButton label="Legacy" active={tab === "legacy"} onClick={() => setTab("legacy")} isLegacy />
        </div>

        {/* Nav items for active tab */}
        {tab === "weather" && (
          <>
            <SectionLabel>NEW SYSTEM</SectionLabel>
            {WEATHER_NAV.map(({ to, label, sub }) => (
              <NavLink
                key={to}
                to={to}
                end={false}
                className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`.trim()}
              >
                <span>{label}</span>
                <span style={{ fontSize: 10, color: "var(--muted)", marginLeft: 6 }}>{sub}</span>
              </NavLink>
            ))}
          </>
        )}

        {tab === "legacy" && (
          <>
            <SectionLabel>LEGACY · 旧系统</SectionLabel>
            <div style={{
              margin: "4px 10px 12px", padding: "6px 10px",
              background: "var(--bad)18", border: "1px solid var(--bad)44",
              borderRadius: 6, fontSize: 10, color: "var(--bad)", lineHeight: 1.5,
            }}>
              ⚠ Deprecated · stopped 2026-05-14<br />
              Under reconstruction
            </div>
            {LEGACY_NAV.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`.trim()}
                style={{ opacity: 0.55 }}
              >
                {label}
              </NavLink>
            ))}
          </>
        )}
      </aside>

      <main className="content">
        <header className="page-head">
          <div>
            <h1 className="page-title">{title}</h1>
            <p className="page-desc">{desc}</p>
          </div>
          <div className="clock mono">{clock}</div>
        </header>
        {children}
      </main>
    </div>
  );
}

function TabButton({ label, active, onClick, isLegacy }: {
  label: string;
  active: boolean;
  onClick: () => void;
  isLegacy?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        flex: 1,
        padding: "5px 4px",
        fontSize: 11, fontWeight: active ? 700 : 400,
        border: "none", cursor: "pointer",
        background: active ? (isLegacy ? "var(--muted)" : "var(--accent)") : "transparent",
        color: active ? "#fff" : isLegacy ? "var(--muted)" : "inherit",
        transition: "all 0.15s",
      }}
    >
      {label}
      {isLegacy && <span style={{ marginLeft: 3, fontSize: 9, opacity: 0.7 }}>↙</span>}
    </button>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      margin: "0 0 6px", fontSize: 10,
      color: "var(--muted)", padding: "0 12px",
      letterSpacing: "0.08em",
    }}>
      {children}
    </div>
  );
}
