import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./AppShell";

const TodayOverviewPage = lazy(() => import("../pages/v2/TodayOverviewPage").then((m) => ({ default: m.TodayOverviewPage })));
const ProbesPage = lazy(() => import("../pages/v2/ProbesPage").then((m) => ({ default: m.ProbesPage })));
const ProbeDetailPage = lazy(() => import("../pages/v2/ProbeDetailPage").then((m) => ({ default: m.ProbeDetailPage })));
const ResearchLinesPage = lazy(() => import("../pages/v2/ResearchLinesPage").then((m) => ({ default: m.ResearchLinesPage })));
const ResearchLineDetailPage = lazy(() => import("../pages/v2/ResearchLineDetailPage").then((m) => ({ default: m.ResearchLineDetailPage })));
const PerformancePage = lazy(() => import("../pages/v2/PerformancePage").then((m) => ({ default: m.PerformancePage })));
const DailyLineagePage = lazy(() => import("../pages/v2/DailyLineagePage").then((m) => ({ default: m.DailyLineagePage })));
const GlossaryPage = lazy(() => import("../pages/v2/GlossaryPage").then((m) => ({ default: m.GlossaryPage })));
const DataSourcesPage = lazy(() => import("../pages/v2/DataSourcesPage").then((m) => ({ default: m.DataSourcesPage })));
const ArchivePage = lazy(() => import("../pages/v2/ArchivePage").then((m) => ({ default: m.ArchivePage })));
const AccountsPage = lazy(() => import("../pages/AccountsPage").then((m) => ({ default: m.AccountsPage })));
const BacktestsPage = lazy(() => import("../pages/BacktestsPage").then((m) => ({ default: m.BacktestsPage })));
const DashboardPage = lazy(() => import("../pages/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const InstanceDetailPage = lazy(() => import("../pages/InstanceDetailPage").then((m) => ({ default: m.InstanceDetailPage })));
const StrategiesPage = lazy(() => import("../pages/StrategiesPage").then((m) => ({ default: m.StrategiesPage })));
const StrategyDetailPage = lazy(() => import("../pages/StrategyDetailPage").then((m) => ({ default: m.StrategyDetailPage })));
const WeatherRunsPage = lazy(() => import("../pages/weather/WeatherRunsPage").then((m) => ({ default: m.WeatherRunsPage })));
const WeatherComparePage = lazy(() => import("../pages/weather/WeatherComparePage").then((m) => ({ default: m.WeatherComparePage })));
const WeatherHistoryPage = lazy(() => import("../pages/weather/WeatherHistoryPage").then((m) => ({ default: m.WeatherHistoryPage })));
const WeatherLivePage = lazy(() => import("../pages/weather/WeatherLivePage").then((m) => ({ default: m.WeatherLivePage })));
const WeatherOrderBlotterPage = lazy(() => import("../pages/weather/WeatherOrderBlotterPage").then((m) => ({ default: m.WeatherOrderBlotterPage })));
const WeatherResearchPage = lazy(() => import("../pages/weather/WeatherResearchPage").then((m) => ({ default: m.WeatherResearchPage })));
const WeatherStrategyRuntimePage = lazy(() => import("../pages/weather/WeatherStrategyRuntimePage").then((m) => ({ default: m.WeatherStrategyRuntimePage })));
const WeatherTradeDrilldownPage = lazy(() => import("../pages/weather/WeatherTradeDrilldownPage").then((m) => ({ default: m.WeatherTradeDrilldownPage })));
const WeatherConfigsPage = lazy(() => import("../pages/weather/WeatherStrategiesPage").then((m) => ({ default: m.WeatherConfigsPage })));
const WeatherConfigDetailPage = lazy(() => import("../pages/weather/WeatherStrategyDetailPage").then((m) => ({ default: m.WeatherConfigDetailPage })));
const WeatherStrategyManagementPage = lazy(() => import("../pages/weather/WeatherStrategyManagementPage").then((m) => ({ default: m.WeatherStrategyManagementPage })));
const WeatherStrategyDefinitionDetailPage = lazy(() => import("../pages/weather/WeatherStrategyDefinitionDetailPage").then((m) => ({ default: m.WeatherStrategyDefinitionDetailPage })));
const WeatherStrategyInstancesPage = lazy(() => import("../pages/weather/WeatherStrategyInstancesPage").then((m) => ({ default: m.WeatherStrategyInstancesPage })));
const WeatherStrategyInstanceDetailPage = lazy(() => import("../pages/weather/WeatherStrategyInstanceDetailPage").then((m) => ({ default: m.WeatherStrategyInstanceDetailPage })));
const CopyTradeWalletsPage = lazy(() => import("../pages/weather/CopyTradeWalletsPage").then((m) => ({ default: m.CopyTradeWalletsPage })));
const CopyTradeWalletDetailPage = lazy(() => import("../pages/weather/CopyTradeWalletDetailPage").then((m) => ({ default: m.CopyTradeWalletDetailPage })));
const CapitalEfficiencyPage = lazy(() => import("../pages/weather/CapitalEfficiencyPage").then((m) => ({ default: m.CapitalEfficiencyPage })));

export function App(): JSX.Element {
  return (
    <Suspense fallback={<div style={{ padding: 24 }}>Loading…</div>}>
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
      <Route path="/weather/strategies" element={<WeatherStrategyManagementPage />} />
      <Route path="/weather/strategies/:strategyKey" element={<WeatherStrategyDefinitionDetailPage />} />
      <Route path="/weather/configs" element={<WeatherConfigsPage />} />
      <Route path="/weather/configs/:configId" element={<WeatherConfigDetailPage />} />
      <Route path="/weather/instances" element={<WeatherStrategyInstancesPage />} />
      <Route path="/weather/instances/:instanceId" element={<WeatherStrategyInstanceDetailPage />} />
      <Route path="/weather/runs" element={<WeatherRunsPage />} />
      <Route path="/weather/compare" element={<WeatherComparePage />} />
      <Route path="/weather/research" element={<WeatherResearchPage />} />
      <Route path="/weather/history/:runId" element={<WeatherHistoryPage />} />
      <Route path="/weather/trade/:runId/:signalId" element={<WeatherTradeDrilldownPage />} />
      <Route path="/weather/live" element={<WeatherLivePage />} />
      <Route path="/weather/orders" element={<WeatherOrderBlotterPage />} />
      <Route path="/weather/runtime" element={<WeatherStrategyRuntimePage />} />
      <Route path="/copy-trade/wallets" element={<CopyTradeWalletsPage />} />
      <Route path="/copy-trade/wallets/:walletAddress" element={<CopyTradeWalletDetailPage />} />
      <Route path="/copy-trade/capital-efficiency" element={<CapitalEfficiencyPage />} />

      {/* Legacy PMM/ARB pages, namespaced to avoid colliding with v2 /research etc. */}
      <Route path="/legacy/dashboard" element={<DashboardPage />} />
      <Route path="/legacy/strategies" element={<StrategiesPage />} />
      <Route path="/legacy/strategies/:strategyKey" element={<StrategyDetailPage />} />
      <Route path="/legacy/instances/:instanceId" element={<InstanceDetailPage />} />
      <Route path="/legacy/accounts" element={<AccountsPage />} />
      <Route path="/legacy/backtests" element={<BacktestsPage />} />

      <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
}
