import { Link } from "react-router-dom";

const LEGACY = [
  { to: "/weather/strategies", label: "Weather · Strategies", note: "旧策略列表" },
  { to: "/weather/runs", label: "Weather · Runs", note: "运行记录" },
  { to: "/weather/live", label: "Weather · Live (旧)", note: "旧实盘监控页" },
  { to: "/weather/runtime", label: "Weather · Runtime", note: "策略运行态" },
  { to: "/weather/compare", label: "Weather · Compare", note: "对比" },
  { to: "/weather/research", label: "Weather · Research", note: "shadow 研究(旧)" },
  { to: "/copy-trade/wallets", label: "Copy Trade · Wallets", note: "跟单钱包" },
  { to: "/legacy/dashboard", label: "Legacy · Dashboard", note: "旧 PMM/ARB 总览" },
  { to: "/legacy/strategies", label: "Legacy · Strategies", note: "旧 PMM/ARB 策略" },
  { to: "/legacy/accounts", label: "Legacy · Accounts", note: "旧账户" },
  { to: "/legacy/backtests", label: "Legacy · Backtests", note: "旧回测" },
];

export function ArchivePage() {
  return (
    <div className="page">
      <header className="page-head">
        <h1>归档 / legacy</h1>
        <div className="gate-banner gate-warn">
          这些是旧 PMM/ARB 与上一版天气页面，<strong>非当前主线（dormant，保留备用不删）</strong>。新工作请用左侧主导航。
        </div>
      </header>
      <div className="archive-grid">
        {LEGACY.map((it) => (
          <Link key={it.to} to={it.to} className="card archive-item">
            <div className="archive-label">{it.label}</div>
            <div className="archive-note muted">{it.note}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}
