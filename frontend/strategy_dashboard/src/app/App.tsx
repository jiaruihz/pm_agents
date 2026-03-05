import { Navigate, Route, Routes } from "react-router-dom";
import { AccountsPage } from "../pages/AccountsPage";
import { BacktestsPage } from "../pages/BacktestsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { InstanceDetailPage } from "../pages/InstanceDetailPage";
import { ResearchPage } from "../pages/ResearchPage";
import { StrategiesPage } from "../pages/StrategiesPage";
import { StrategyDetailPage } from "../pages/StrategyDetailPage";

export function App(): JSX.Element {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<DashboardPage />} />
      <Route path="/strategies" element={<StrategiesPage />} />
      <Route path="/strategies/:strategyKey" element={<StrategyDetailPage />} />
      <Route path="/instances/:instanceId" element={<InstanceDetailPage />} />
      <Route path="/accounts" element={<AccountsPage />} />
      <Route path="/research" element={<ResearchPage />} />
      <Route path="/backtests" element={<BacktestsPage />} />
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}
