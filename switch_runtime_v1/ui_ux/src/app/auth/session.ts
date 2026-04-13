export type AuthSession = {
  username: string;
  loginAt: string;
};

const SESSION_KEY = "quanto_exchange_session_v1";

function _safeWindow(): Window | null {
  return typeof window === "undefined" ? null : window;
}

function _env(name: string): string {
  const value = (import.meta as { env?: Record<string, string | undefined> }).env?.[name];
  return (value || "").trim();
}

export function configuredUsername(): string {
  return _env("VITE_UI_USERNAME") || "admin";
}

export function configuredPassword(): string {
  return _env("VITE_UI_PASSWORD") || "password";
}

export function getSession(): AuthSession | null {
  const w = _safeWindow();
  if (!w) return null;
  try {
    const raw = w.localStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<AuthSession>;
    if (!parsed || !parsed.username || !parsed.loginAt) return null;
    return { username: String(parsed.username), loginAt: String(parsed.loginAt) };
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  return !!getSession();
}

export function login(username: string, password: string): { ok: boolean; message?: string } {
  const u = username.trim();
  const p = password;
  const allowedUser = configuredUsername();
  const allowedPass = configuredPassword();
  if (!u || !p) {
    return { ok: false, message: "Username and password are required." };
  }
  if (u !== allowedUser || p !== allowedPass) {
    return { ok: false, message: "Invalid username or password." };
  }
  const w = _safeWindow();
  if (!w) return { ok: false, message: "Browser session unavailable." };
  const session: AuthSession = { username: u, loginAt: new Date().toISOString() };
  w.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  return { ok: true };
}

export function logout(): void {
  const w = _safeWindow();
  if (!w) return;
  w.localStorage.removeItem(SESSION_KEY);
}

