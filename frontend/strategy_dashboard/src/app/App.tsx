import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";
// v2 pages
import { TodayOverviewPage } from "../pages/v2/TodayOverviewPage";
import { ProbesPage } from "../pages/v2/ProbesPage";
import { ProbeDetailPage } from "../pages/v2/ProbeDetailPage";
import { ResearchLinesPage } from "../pages/v2/ResearchLinesPage";
import { ResearchLineDetailPage } from "../pages/v2/ResearchLineDetailPage";
import { PerformancePage } from "../pages/v2/PerformancePage";
import { DailyLineagePage } from "../pages/v2/DailyLineagePage";
import { GlossaryPage } from "../pages/v2/GlossaryPage";
import { DataSourcesPage } from "../pages/v2/DataSourcesPage";
import { ArchivePage } from "../pages/v2/ArchivePage";
// legacy pages (kept reachable from /archive)
import { AccountsPage } from "../pages/AccountsPage";
import { BacktestsPage } from "../pages/BacktestsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { InstanceDetailPage } from "../pages/InstanceDetailPage";
import { StrategiesPage } from "../pages/StrategiesPage";
import { StrategyDetailPage } from "../pages/StrategyDetailPage";
import { WeatherRunsPage } from "../pages/weather/WeatherRunsPage";
import { WeatherComparePage } from "../pages/weather/WeatherComparePage";
import { WeatherHistoryPage } from "../pages/weather/WeatherHistoryPage";
import { WeatherLivePage } from "../pages/weather/WeatherLivePage";
import { WeatherResearchPage } from "../pages/weather/WeatherResearchPage";
import { WeatherStrategyRuntimePage } from "../pages/weather/WeatherStrategyRuntimePage";
import { WeatherTradeDrilldownPage } from "../pages/weather/WeatherTradeDrilldownPage";
import { WeatherStrategiesPage } from "../pages/weather/WeatherStrategiesPage";
import { WeatherStrategyDetailPage } from "../pages/weather/WeatherStrategyDetailPage";
import { CopyTradeWalletsPage } from "../pages/weather/CopyTradeWalletsPage";
import { CopyTradeWalletDetailPage } from "../pages/weather/CopyTradeWalletDetailPage";

export function App(): JSX.Element {
  return (
    <Routes>
      {/* v2 redesign (default) */}
      <Route element={<AppShell />}>
        <Route path="/" element={<TodayOverviewPage />} />
        <Route path="/probes" element={<ProbesPage />} />
        <Route path="/probes/:instance" element={<ProbeDetailPage />} />
        <Route path="/research" element={<ResearchLinesPage />} />
        <Route path="/research/:lineId" element={<ResearchLineDetailPage />} />
        <Route path="/performance" element={<PerformancePage />} />
        <Route path="/lineage" element={<DailyLineagePage />} />
        <Route path="/lineage/:date" element={<DailyLineagePage />} />
        <Route path="/data-sources" element={<DataSourcesPage />} />
        <Route path="/glossary" element={<GlossaryPage />} />
        <Route path="/archive" element={<ArchivePage />} />
      </Route>

      {/* Legacy weather pages (reachable via /archive, keep original routes) */}
      <Route path="/weather/strategies" element={<WeatherStrategiesPage />} />
      <Route path="/weather/strategies/:configId" element={<WeatherStrategyDetailPage />} />
      <Route path="/weather/runs" element={<WeatherRunsPage />} />
      <Route path="/weather/compare" element={<WeatherComparePage />} />
      <Route path="/weather/research" element={<WeatherResearchPage />} />
      <Route path="/weather/history/:runId" element={<WeatherHistoryPage />} />
      <Route path="/weather/trade/:runId/:signalId" element={<WeatherTradeDrilldownPage />} />
      <Route path="/weather/live" element={<WeatherLivePage />} />
      <Route path="/weather/runtime" element={<WeatherStrategyRuntimePage />} />
      <Route path="/copy-trade/wallets" element={<CopyTradeWalletsPage />} />
      <Route path="/copy-trade/wallets/:walletAddress" element={<CopyTradeWalletDetailPage />} />

      {/* Legacy PMM/ARB pages, namespaced to avoid colliding with v2 /research etc. */}
      <Route path="/legacy/dashboard" element={<DashboardPage />} />
      <Route path="/legacy/strategies" element={<StrategiesPage />} />
      <Route path="/legacy/strategies/:strategyKey" element={<StrategyDetailPage />} />
      <Route path="/legacy/instances/:instanceId" element={<InstanceDetailPage />} />
      <Route path="/legacy/accounts" element={<AccountsPage />} />
      <Route path="/legacy/backtests" element={<BacktestsPage />} />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
