import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./app/App";
import { DashboardProviderRoot } from "./data/provider-context";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <DashboardProviderRoot>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </DashboardProviderRoot>
  </React.StrictMode>
);
