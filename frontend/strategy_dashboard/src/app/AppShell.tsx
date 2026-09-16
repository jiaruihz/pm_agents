import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import { GlossaryProvider } from "../data/glossary-context";

const MAIN_NAV = [
  { to: "/", label: "今日总览", sub: "脉搏", end: true },
  { to: "/probes", label: "探针在跑", sub: "健康 / 执行质量" },
  { to: "/research", label: "研究证据", sub: "前向证据登记" },
  { to: "/performance", label: "绩效对账", sub: "已结算 / 现金流" },
  { to: "/lineage", label: "单日血缘", sub: "逐笔复盘" },
  { to: "/data-sources", label: "数据源", sub: "抓取来源 / 时间 / 城市" },
];

const SECONDARY_NAV = [
  { to: "/glossary", label: "术语字典", sub: "字段口径" },
  { to: "/archive", label: "归档", sub: "legacy / dormant" },
];

function useClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

export function AppShell() {
  const clock = useClock();
  const { pathname } = useLocation();
  const isArchive = pathname.startsWith("/archive");

  return (
    <GlossaryProvider>
      <div className="shell">
        <aside className="nav">
          <div className="nav-brand">
            <div className="nav-brand-title">天气策略台</div>
            <div className="nav-brand-sub">probe-lifecycle console</div>
          </div>

          <nav className="nav-group">
            {MAIN_NAV.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end} className="nav-item">
                <span className="nav-item-label">{it.label}</span>
                <span className="nav-item-sub">{it.sub}</span>
              </NavLink>
            ))}
          </nav>

          <div className="nav-spacer" />

          <nav className="nav-group secondary">
            {SECONDARY_NAV.map((it) => (
              <NavLink key={it.to} to={it.to} className="nav-item">
                <span className="nav-item-label">{it.label}</span>
                <span className="nav-item-sub">{it.sub}</span>
              </NavLink>
            ))}
          </nav>

          <div className="nav-clock">
            <div>{clock.toLocaleTimeString("zh-CN")}</div>
            <div className="nav-clock-utc">{clock.toISOString().slice(11, 19)} UTC</div>
          </div>
        </aside>

        <main className={`content${isArchive ? " content-archive" : ""}`}>
          <Outlet />
        </main>
      </div>
    </GlossaryProvider>
  );
}
