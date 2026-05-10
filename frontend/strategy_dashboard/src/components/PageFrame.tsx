import { NavLink } from "react-router-dom";

const NAV_ITEMS = [
  { to: "/dashboard", label: "总览" },
  { to: "/strategies", label: "策略" },
  { to: "/accounts", label: "账户" },
  { to: "/research", label: "Research" },
  { to: "/backtests", label: "Backtests" },
];

export function PageFrame({ title, desc, children }: { title: string; desc: string; children: JSX.Element }): JSX.Element {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">STRAT OPS</div>
        <div className="brand-sub">Strategy Runtime Console</div>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`.trim()}
          >
            {item.label}
          </NavLink>
        ))}
      </aside>
      <main className="content">
        <header className="page-head">
          <div>
            <h1 className="page-title">{title}</h1>
            <p className="page-desc">{desc}</p>
          </div>
          <div className="clock mono">{new Date().toLocaleString()}</div>
        </header>
        {children}
      </main>
    </div>
  );
}
