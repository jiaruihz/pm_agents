import { Navigate, Route, Routes } from "react-router-dom";
import { AccountsPage } from "../pages/AccountsPage";
import { BacktestsPage } from "../pages/BacktestsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { InstanceDetailPage } from "../pages/InstanceDetailPage";
import { ResearchPage } from "../pages/ResearchPage";
import { StrategiesPage } from "../pages/StrategiesPage";
import { StrategyDetailPage } from "../pages/StrategyDetailPage";
import { WeatherRunsPage } from "../pages/weather/WeatherRunsPage";
import { WeatherStrategiesPage } from "../pages/weather/WeatherStrategiesPage";
import { WeatherComparePage } from "../pages/weather/WeatherComparePage";
import { WeatherHistoryPage } from "../pages/weather/WeatherHistoryPage";
import { WeatherLivePage } from "../pages/weather/WeatherLivePage";
import { WeatherResearchPage } from "../pages/weather/WeatherResearchPage";
import { WeatherTradeDrilldownPage } from "../pages/weather/WeatherTradeDrilldownPage";
import { WeatherStrategyDetailPage } from "../pages/weather/WeatherStrategyDetailPage";
import { CopyTradeWalletsPage } from "../pages/weather/CopyTradeWalletsPage";
import { CopyTradeWalletDetailPage } from "../pages/weather/CopyTradeWalletDetailPage";

export function App(): JSX.Element {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/weather/strategies" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/strategies" element={<StrategiesPage />} />
      <Route path="/strategies/:strategyKey" element={<StrategyDetailPage />} />
      <Route path="/instances/:instanceId" element={<InstanceDetailPage />} />
      <Route path="/accounts" element={<AccountsPage />} />
      <Route path="/research" element={<ResearchPage />} />
      <Route path="/backtests" element={<BacktestsPage />} />
      {/* Weather Dashboard */}
      <Route path="/weather/strategies" element={<WeatherStrategiesPage />} />
      <Route path="/weather/strategies/:configId" element={<WeatherStrategyDetailPage />} />
      <Route path="/weather/runs" element={<WeatherRunsPage />} />
      <Route path="/weather/compare" element={<WeatherComparePage />} />
      <Route path="/weather/research" element={<WeatherResearchPage />} />
      <Route path="/weather/history/:runId" element={<WeatherHistoryPage />} />
      <Route path="/weather/trade/:runId/:signalId" element={<WeatherTradeDrilldownPage />} />
      <Route path="/weather/live" element={<WeatherLivePage />} />
      <Route path="/weather/copy-trade" element={<Navigate to="/copy-trade/wallets" replace />} />
      <Route path="/weather" element={<Navigate to="/weather/strategies" replace />} />
      {/* Copy Trade */}
      <Route path="/copy-trade/wallets" element={<CopyTradeWalletsPage />} />
      <Route path="/copy-trade/wallets/:walletAddress" element={<CopyTradeWalletDetailPage />} />
      <Route path="/copy-trade" element={<Navigate to="/copy-trade/wallets" replace />} />
      <Route path="*" element={<Navigate to="/weather/strategies" replace />} />
    </Routes>
  );
}
