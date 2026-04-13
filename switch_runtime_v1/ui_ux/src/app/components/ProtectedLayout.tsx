import { Navigate, useLocation } from "react-router";
import { isAuthenticated } from "../auth/session";
import { Layout } from "./Layout";

export function ProtectedLayout() {
  const location = useLocation();
  if (!isAuthenticated()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <Layout />;
}

