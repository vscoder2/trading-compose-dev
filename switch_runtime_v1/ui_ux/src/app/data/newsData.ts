import runtimeSnapshotRaw from "./runtime_snapshot.json";

export interface NewsItem {
  id: string;
  title: string;
  source: string;
  timestamp: string;
  category: "market" | "stock" | "ai" | "earnings";
  sentiment: "positive" | "negative" | "neutral";
  impact: "high" | "medium" | "low";
  summary: string;
  relatedStocks: string[];
}

export interface Notification {
  id: string;
  type: "trade" | "alert" | "bot" | "market";
  title: string;
  message: string;
  timestamp: string;
  read: boolean;
  priority: "high" | "medium" | "low";
}

type RuntimeSnapshot = {
  generatedAt?: string;
  summary?: {
    maxDrawdownPct?: number;
    profile?: string;
    variant?: string;
  };
  trades?: Array<{
    id?: string;
    symbol?: string;
    type?: string;
    shares?: number;
    price?: number;
    timestamp?: string;
    strategy?: string;
    profit?: number;
  }>;
};

const runtime = (runtimeSnapshotRaw || {}) as RuntimeSnapshot;
const trades = Array.isArray(runtime.trades) ? runtime.trades : [];

function _num(v: unknown, d = 0): number {
  const n = Number(v);
  return Number.isFinite(n) ? n : d;
}

function _sentimentFromTrade(trade: { profit?: number; type?: string }): "positive" | "negative" | "neutral" {
  const p = _num(trade.profit, 0);
  if (p > 0) return "positive";
  if (p < 0) return "negative";
  return String(trade.type || "").toLowerCase() === "buy" ? "neutral" : "negative";
}

function _impactFromTrade(trade: { shares?: number; price?: number }): "high" | "medium" | "low" {
  const notional = _num(trade.shares, 0) * _num(trade.price, 0);
  if (notional >= 20000) return "high";
  if (notional >= 5000) return "medium";
  return "low";
}

export const mockNews: NewsItem[] = trades
  .slice(-20)
  .reverse()
  .map((t, i) => {
    const side = String(t.type || "trade").toUpperCase();
    const symbol = String(t.symbol || "N/A").toUpperCase();
    const shares = _num(t.shares, 0);
    const price = _num(t.price, 0);
    return {
      id: String(t.id || `news-${i + 1}`),
      title: `${symbol} ${side} execution`,
      source: "Runtime Event Stream",
      timestamp: String(t.timestamp || ""),
      category: "stock",
      sentiment: _sentimentFromTrade(t),
      impact: _impactFromTrade(t),
      summary: `${side} ${shares.toLocaleString()} ${symbol} @ $${price.toFixed(4)} via ${String(t.strategy || "runtime")}.`,
      relatedStocks: symbol && symbol !== "N/A" ? [symbol] : [],
    };
  });

const riskAlert = _num(runtime.summary?.maxDrawdownPct, 0) >= 10
  ? [
      {
        id: "risk-dd",
        type: "alert" as const,
        title: "Drawdown Threshold Warning",
        message: `Max drawdown is ${_num(runtime.summary?.maxDrawdownPct, 0).toFixed(2)}%.`,
        timestamp: String(runtime.generatedAt || new Date().toISOString()),
        read: false,
        priority: "high" as const,
      },
    ]
  : [];

export const mockNotifications: Notification[] = [
  ...trades.slice(-15).reverse().map((t, i) => {
    const side = String(t.type || "trade").toUpperCase();
    const symbol = String(t.symbol || "N/A").toUpperCase();
    const shares = _num(t.shares, 0);
    return {
      id: String(t.id || `notif-${i + 1}`),
      type: "trade" as const,
      title: `${side} ${symbol}`,
      message: `${side} ${shares.toLocaleString()} shares @ $${_num(t.price, 0).toFixed(4)}.`,
      timestamp: String(t.timestamp || ""),
      read: i > 2,
      priority: _impactFromTrade(t),
    };
  }),
  ...riskAlert,
];

