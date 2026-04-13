import runtimeSnapshotRaw from "./runtime_snapshot.json";
import { toFriendlyProfileName, toFriendlyStrategyLabel } from "./profileLabels";

export interface Stock {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap: string;
}

export interface Position {
  symbol: string;
  name: string;
  shares: number;
  avgPrice: number;
  currentPrice: number;
  totalValue: number;
  gainLoss: number;
  gainLossPercent: number;
}

export interface Trade {
  id: string;
  symbol: string;
  type: "buy" | "sell";
  shares: number;
  price: number;
  timestamp: string;
  strategy: string;
  profit?: number;
}

export interface TradingBot {
  id: string;
  name: string;
  strategy: string;
  status: "active" | "paused" | "stopped";
  totalTrades: number;
  winRate: number;
  profit: number;
  riskLevel: "low" | "medium" | "high";
}

export interface RuntimeSummary {
  portfolioValue: number;
  todayPnL: number;
  todayPnLPct: number;
  activeBots: number;
  totalBotProfit: number;
  todaysTrades: number;
  openAlerts: number;
  maxDrawdownPct: number;
  profile: string;
  profileId?: string;
  variant: string;
}

type RuntimeSnapshot = {
  generatedAt?: string;
  sourceDb?: string;
  summary?: Partial<RuntimeSummary>;
  stocks?: Stock[];
  positions?: Position[];
  trades?: Trade[];
  bots?: TradingBot[];
  portfolioChart?: Array<{ date: string; value: number }>;
};

const runtimeSnapshot: RuntimeSnapshot = (runtimeSnapshotRaw || {}) as RuntimeSnapshot;

function _num(v: unknown, d = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function _stocks(): Stock[] {
  const rows = Array.isArray(runtimeSnapshot.stocks) ? runtimeSnapshot.stocks : [];
  return rows.map((s) => ({
    symbol: String(s.symbol || "N/A"),
    name: String(s.name || s.symbol || "N/A"),
    price: _num(s.price),
    change: _num(s.change),
    changePercent: _num(s.changePercent),
    volume: _num(s.volume),
    marketCap: String(s.marketCap || "N/A"),
  }));
}

function _positions(): Position[] {
  const rows = Array.isArray(runtimeSnapshot.positions) ? runtimeSnapshot.positions : [];
  return rows.map((p) => ({
    symbol: String(p.symbol || "N/A"),
    name: String(p.name || p.symbol || "N/A"),
    shares: _num(p.shares),
    avgPrice: _num(p.avgPrice),
    currentPrice: _num(p.currentPrice),
    totalValue: _num(p.totalValue),
    gainLoss: _num(p.gainLoss),
    gainLossPercent: _num(p.gainLossPercent),
  }));
}

function _trades(): Trade[] {
  const rows = Array.isArray(runtimeSnapshot.trades) ? runtimeSnapshot.trades : [];
  return rows.map((t, idx) => ({
    id: String(t.id || `trade-${idx + 1}`),
    symbol: String(t.symbol || "N/A"),
    type: String(t.type || "buy").toLowerCase() === "sell" ? "sell" : "buy",
    shares: _num(t.shares),
    price: _num(t.price),
    timestamp: String(t.timestamp || ""),
    strategy: toFriendlyStrategyLabel(String(t.strategy || "runtime")),
    profit: t.profit === undefined ? undefined : _num(t.profit),
  }));
}

function _bots(): TradingBot[] {
  const rows = Array.isArray(runtimeSnapshot.bots) ? runtimeSnapshot.bots : [];
  return rows.map((b, idx) => {
    const status = String(b.status || "active").toLowerCase();
    const risk = String(b.riskLevel || "medium").toLowerCase();
    return {
      id: String(b.id || `bot-${idx + 1}`),
      name: String(b.name || `Runtime Bot ${idx + 1}`),
      strategy: toFriendlyStrategyLabel(String(b.strategy || "runtime")),
      status: status === "paused" || status === "stopped" ? (status as "paused" | "stopped") : "active",
      totalTrades: _num(b.totalTrades),
      winRate: _num(b.winRate),
      profit: _num(b.profit),
      riskLevel: risk === "low" || risk === "high" ? (risk as "low" | "high") : "medium",
    };
  });
}

function _summary(): RuntimeSummary {
  const s = runtimeSnapshot.summary || {};
  return {
    portfolioValue: _num(s.portfolioValue, 0),
    todayPnL: _num(s.todayPnL, 0),
    todayPnLPct: _num(s.todayPnLPct, 0),
    activeBots: _num(s.activeBots, 0),
    totalBotProfit: _num(s.totalBotProfit, 0),
    todaysTrades: _num(s.todaysTrades, 0),
    openAlerts: _num(s.openAlerts, 0),
    maxDrawdownPct: _num(s.maxDrawdownPct, 0),
    profile: toFriendlyProfileName(String(s.profile || "")),
    profileId: String((s as Record<string, unknown>).profileId || ""),
    variant: String(s.variant || ""),
  };
}

export const mockStocks: Stock[] = _stocks();
export const mockPositions: Position[] = _positions();
export const mockTrades: Trade[] = _trades();
export const mockBots: TradingBot[] = _bots();
export const runtimeSummary: RuntimeSummary = _summary();

export function generateChartData(days: number = 30) {
  const runtimeCurve = Array.isArray(runtimeSnapshot.portfolioChart) ? runtimeSnapshot.portfolioChart : [];
  const trimmed = runtimeCurve.slice(-Math.max(5, days));
  return trimmed.map((p) => ({
    date: new Date(p.date).toLocaleDateString("en-US", { month: "short", day: "numeric" }),
    value: Math.round(_num(p.value)),
  }));
}

export function generatePriceData(symbol: string, days: number = 7) {
  const trades = mockTrades
    .filter((t) => t.symbol === symbol && _num(t.price) > 0 && !!t.timestamp)
    .map((t) => ({ ts: new Date(t.timestamp), price: _num(t.price) }))
    .filter((t) => !Number.isNaN(t.ts.getTime()))
    .sort((a, b) => a.ts.getTime() - b.ts.getTime());

  if (!trades.length) {
    const stock = mockStocks.find((s) => s.symbol === symbol);
    if (!stock) return [];
    return [
      {
        time: new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
        price: parseFloat(stock.price.toFixed(4)),
      },
    ];
  }

  const since = Date.now() - days * 24 * 60 * 60 * 1000;
  return trades
    .filter((t) => t.ts.getTime() >= since)
    .map((t) => ({
      time: t.ts.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }),
      price: parseFloat(t.price.toFixed(4)),
    }));
}
