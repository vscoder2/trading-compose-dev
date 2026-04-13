import { createBrowserRouter } from "react-router";
import { Dashboard } from "./pages/Dashboard";
import { Portfolio } from "./pages/Portfolio";
import { AutoTrading } from "./pages/AutoTrading";
import { Analytics } from "./pages/Analytics";
import { Backtesting } from "./pages/Backtesting";
import { PaperTrading } from "./pages/PaperTrading";
import { Login } from "./pages/Login";
import { ProtectedLayout } from "./components/ProtectedLayout";

export const router = createBrowserRouter([
  {
    path: "/login",
    Component: Login,
  },
  {
    path: "/",
    Component: ProtectedLayout,
    children: [
      { index: true, Component: Dashboard },
      { path: "portfolio", Component: Portfolio },
      { path: "auto-trading", Component: AutoTrading },
      { path: "analytics", Component: Analytics },
      { path: "backtesting", Component: Backtesting },
      { path: "paper-trading", Component: PaperTrading },
    ],
  },
]);
