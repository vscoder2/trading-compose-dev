import { Link, Outlet, useLocation } from "react-router";
import { LayoutDashboard, Briefcase, Bot, BarChart3, TrendingUp, FlaskConical, Wallet, LogOut } from "lucide-react";
import { cn } from "./ui/utils";
import { NotificationCenter } from "./NotificationCenter";
import { logout, getSession } from "../auth/session";
import { Button } from "./ui/button";
import { useNavigate } from "react-router";

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const session = getSession();

  const navItems = [
    { path: "/", label: "Dashboard", icon: LayoutDashboard },
    { path: "/portfolio", label: "Portfolio", icon: Briefcase },
    { path: "/auto-trading", label: "AI Trading", icon: Bot },
    { path: "/analytics", label: "Analytics", icon: BarChart3 },
    { path: "/backtesting", label: "Backtesting", icon: FlaskConical },
    { path: "/paper-trading", label: "Paper Trading", icon: Wallet },
  ];

  return (
    <div className="min-h-screen bg-slate-950">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/50 backdrop-blur-sm sticky top-0 z-10">
        <div className="container mx-auto px-6 py-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-blue-600 rounded-lg">
                <TrendingUp className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-xl text-white font-semibold">Quantro Exchange</h1>
                <p className="text-xs text-slate-400">Runtime Trading Platform</p>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="hidden md:block text-xs text-slate-400">
                Signed in as <span className="text-slate-200">{session?.username || "user"}</span>
              </div>
              <div className="px-4 py-2 bg-green-600/20 border border-green-600/50 rounded-lg">
                <p className="text-sm text-green-400">Market: Open</p>
              </div>
              <NotificationCenter />
              <Button
                variant="outline"
                size="sm"
                className="border-slate-700 text-slate-300 hover:bg-slate-800"
                onClick={() => {
                  logout();
                  navigate("/login", { replace: true });
                }}
              >
                <LogOut className="w-4 h-4 mr-2" />
                Logout
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="flex">
        {/* Sidebar */}
        <aside className="w-64 border-r border-slate-800 bg-slate-900/30 min-h-[calc(100vh-73px)] overflow-y-auto">
          <nav className="p-4 space-y-2">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = location.pathname === item.path;
              return (
                <Link
                  key={item.path}
                  to={item.path}
                  className={cn(
                    "flex items-center gap-3 px-4 py-3 rounded-lg transition-colors",
                    isActive
                      ? "bg-blue-600 text-white"
                      : "text-slate-400 hover:bg-slate-800 hover:text-white"
                  )}
                >
                  <Icon className="w-5 h-5" />
                  <span className="text-sm">{item.label}</span>
                </Link>
              );
            })}
          </nav>
        </aside>

        {/* Main Content */}
        <main className="flex-1 p-6 overflow-y-auto max-h-[calc(100vh-73px)]">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
