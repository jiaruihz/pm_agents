import React, { createContext, useContext, useMemo } from "react";
import type { DashboardProvider } from "./provider";
import { HttpDashboardProvider } from "./http-provider";
import { MockDashboardProvider } from "./mock-provider";

const DashboardProviderContext = createContext<DashboardProvider | null>(null);

export function DashboardProviderRoot({ children }: { children: React.ReactNode }): JSX.Element {
  const mode = (import.meta.env.VITE_DASHBOARD_DATA_MODE ?? "mock").toLowerCase();
  const provider = useMemo<DashboardProvider>(() => {
    if (mode === "http") {
      return new HttpDashboardProvider();
    }
    return new MockDashboardProvider();
  }, [mode]);

  return <DashboardProviderContext.Provider value={provider}>{children}</DashboardProviderContext.Provider>;
}

export function useDashboardProvider(): DashboardProvider {
  const provider = useContext(DashboardProviderContext);
  if (!provider) {
    throw new Error("Dashboard provider missing");
  }
  return provider;
}
