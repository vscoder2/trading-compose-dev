import { FormEvent, useMemo, useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { isAuthenticated, login } from "../auth/session";
import { Eye, EyeOff } from "lucide-react";

export function Login() {
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const redirectTo = useMemo(() => {
    const state = location.state as { from?: string } | null;
    return state?.from || "/";
  }, [location.state]);

  if (isAuthenticated()) {
    return <Navigate to={redirectTo} replace />;
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError("");
    const res = login(username, password);
    if (!res.ok) {
      setError(res.message || "Login failed.");
      setSubmitting(false);
      return;
    }
    navigate(redirectTo, { replace: true });
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-[#060b16] text-slate-100">
      <div className="absolute inset-0 bg-[url('https://images.unsplash.com/photo-1534088568595-a066f410bcda?auto=format&fit=crop&w=2600&q=80')] bg-cover bg-center opacity-30" />
      <div className="absolute inset-0 bg-[linear-gradient(180deg,rgba(4,8,18,0.82),rgba(7,12,23,0.92))]" />

      <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-[1860px] items-center px-2 py-3 md:px-4 md:py-4">
        <div className="grid min-h-[calc(100vh-32px)] w-full grid-cols-1 md:grid-cols-[45.5%_50.5%] md:gap-14">
          <section className="hidden min-h-[calc(100vh-32px)] bg-[#0c3d63]/93 px-12 py-14 text-white md:flex md:flex-col">
            <div className="mb-12 text-[30px] font-semibold leading-none tracking-tight">Quantro Exchange</div>
            <p className="mb-8 max-w-[540px] text-[12px] leading-relaxed text-white/85">
              Unified trading workspace for strategy-driven investing. Monitor decisions, track executions, and
              review performance from one dashboard.
            </p>
            <div className="space-y-14">
              <div className="border-l border-white/35 pl-6">
                <div className="text-[34px] font-semibold leading-none">Automated Trading</div>
                <div className="mt-2 text-[11px] leading-tight text-white/85">
                  Run rule-based strategies with a clean execution workflow.
                </div>
              </div>
              <div className="border-l border-white/35 pl-6">
                <div className="text-[34px] font-semibold leading-none">Paper + Live</div>
                <div className="mt-2 text-[11px] leading-tight text-white/85">
                  Use the same setup for simulation, paper, and live operations.
                </div>
              </div>
              <div className="border-l border-white/35 pl-6">
                <div className="text-[34px] font-semibold leading-none">Risk Aware</div>
                <div className="mt-2 text-[11px] leading-tight text-white/85">
                  Track switches, exposure, and alerts from one control surface.
                </div>
              </div>
            </div>
            <div className="mt-12 max-w-[560px] rounded-md border border-white/20 bg-white/10 px-4 py-4">
              <p className="text-[11px] font-semibold uppercase tracking-wide text-white/90">After You Sign In</p>
              <p className="mt-2 text-[11px] leading-relaxed text-white/80">
                Access strategy health, portfolio allocation, execution timeline, trade logs, and backtest snapshots.
              </p>
              <p className="mt-2 text-[11px] leading-relaxed text-white/80">
                Designed for both first-time users and advanced operators with a single, guided workflow.
              </p>
            </div>
          </section>

          <section className="flex min-h-[calc(100vh-32px)] items-center justify-center bg-[#0b1220]/94 px-8 py-12 md:px-12 md:py-12">
            <div className="w-full max-w-[580px]">
              <h1 className="text-[40px] font-semibold leading-none tracking-tight text-slate-100">Login</h1>
              <p className="mt-4 text-[12px] leading-tight text-slate-300">
                Don't have an account? <span className="font-medium text-[#49c089]">Sign up</span>
              </p>

              <form className="mt-12 space-y-6" onSubmit={onSubmit}>
                <div>
                  <Label htmlFor="login-username" className="text-[11px] font-semibold text-slate-200">
                    Email
                  </Label>
                  <Input
                    id="login-username"
                    autoComplete="username"
                    className="mt-3 h-10 rounded-[4px] border-[#334155] !bg-[#0f172a] px-3.5 text-[12px] text-slate-100 placeholder:text-slate-400"
                    style={{ backgroundColor: "#0f172a" }}
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Enter email"
                  />
                </div>

                <div>
                  <Label htmlFor="login-password" className="text-[11px] font-semibold text-slate-200">
                    Password
                  </Label>
                  <div className="relative mt-3">
                    <Input
                      id="login-password"
                      type={showPassword ? "text" : "password"}
                      autoComplete="current-password"
                      className="h-10 rounded-[4px] border-[#334155] !bg-[#0f172a] px-3.5 pr-10 text-[12px] text-slate-100 placeholder:text-slate-400"
                      style={{ backgroundColor: "#0f172a" }}
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="Enter password"
                    />
                    <button
                      type="button"
                      className="absolute inset-y-0 right-0 flex items-center px-4 text-slate-400 hover:text-slate-200"
                      onClick={() => setShowPassword((v) => !v)}
                      aria-label={showPassword ? "Hide password" : "Show password"}
                    >
                      {showPassword ? <EyeOff className="h-5 w-5" /> : <Eye className="h-5 w-5" />}
                    </button>
                  </div>
                </div>

                <label className="flex items-center gap-2.5 text-[11px] text-slate-300">
                  <input
                    type="checkbox"
                    checked={remember}
                    onChange={(e) => setRemember(e.target.checked)}
                    className="h-5 w-5 rounded border-slate-300 text-[#2f855a] focus:ring-[#2f855a]"
                  />
                  Remember me on this device
                </label>

                {error && <p className="text-sm text-rose-600">{error}</p>}

                <Button
                  type="submit"
                  className="h-10 w-full rounded-[4px] bg-[#2f855a] text-[13px] font-medium text-white hover:bg-[#276f4a]"
                  disabled={submitting}
                >
                  {submitting ? "Logging in..." : "Log in"}
                </Button>

                <p className="text-[11px] font-medium text-[#49c089]">Forgot password?</p>
              </form>

              <p className="mt-14 text-[10px] leading-relaxed text-slate-400">
                You agree to our <span className="underline">Terms of Service</span> and{" "}
                <span className="underline">Privacy Policy</span> when signing up and using Composer.
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
