// Options trading data

export interface OptionChain {
  symbol: string;
  expirationDate: string;
  strike: number;
  callBid: number;
  callAsk: number;
  callVolume: number;
  callOpenInterest: number;
  callIV: number;
  callDelta: number;
  callGamma: number;
  callTheta: number;
  callVega: number;
  putBid: number;
  putAsk: number;
  putVolume: number;
  putOpenInterest: number;
  putIV: number;
  putDelta: number;
  putGamma: number;
  putTheta: number;
  putVega: number;
}

export const optionChains: OptionChain[] = [
  {
    symbol: "AAPL",
    expirationDate: "2026-05-15",
    strike: 175,
    callBid: 5.20,
    callAsk: 5.40,
    callVolume: 1250,
    callOpenInterest: 8540,
    callIV: 28.5,
    callDelta: 0.62,
    callGamma: 0.035,
    callTheta: -0.08,
    callVega: 0.15,
    putBid: 1.80,
    putAsk: 1.95,
    putVolume: 890,
    putOpenInterest: 5230,
    putIV: 30.2,
    putDelta: -0.38,
    putGamma: 0.035,
    putTheta: -0.06,
    putVega: 0.14,
  },
  {
    symbol: "AAPL",
    expirationDate: "2026-05-15",
    strike: 180,
    callBid: 2.90,
    callAsk: 3.10,
    callVolume: 2340,
    callOpenInterest: 12450,
    callIV: 26.8,
    callDelta: 0.45,
    callGamma: 0.042,
    callTheta: -0.10,
    callVega: 0.18,
    putBid: 4.50,
    putAsk: 4.70,
    putVolume: 1560,
    putOpenInterest: 9870,
    putIV: 28.5,
    putDelta: -0.55,
    putGamma: 0.042,
    putTheta: -0.09,
    putVega: 0.17,
  },
  {
    symbol: "AAPL",
    expirationDate: "2026-05-15",
    strike: 185,
    callBid: 1.40,
    callAsk: 1.55,
    callVolume: 980,
    callOpenInterest: 6780,
    callIV: 25.2,
    callDelta: 0.28,
    callGamma: 0.038,
    callTheta: -0.11,
    callVega: 0.16,
    putBid: 7.80,
    putAsk: 8.10,
    putVolume: 650,
    putOpenInterest: 4320,
    putIV: 27.0,
    putDelta: -0.72,
    putGamma: 0.038,
    putTheta: -0.08,
    putVega: 0.15,
  },
];

export interface OptionsFlow {
  id: string;
  symbol: string;
  expiration: string;
  strike: number;
  type: "call" | "put";
  sentiment: "bullish" | "bearish" | "neutral";
  premium: number;
  size: number;
  timestamp: string;
  unusual: boolean;
  aggressor: "buy" | "sell";
}

export const optionsFlow: OptionsFlow[] = [
  {
    id: "1",
    symbol: "NVDA",
    expiration: "2026-05-15",
    strike: 900,
    type: "call",
    sentiment: "bullish",
    premium: 1250000,
    size: 500,
    timestamp: "2026-04-05T09:45:00",
    unusual: true,
    aggressor: "buy",
  },
  {
    id: "2",
    symbol: "TSLA",
    expiration: "2026-04-18",
    strike: 250,
    type: "put",
    sentiment: "bearish",
    premium: 875000,
    size: 350,
    timestamp: "2026-04-05T10:15:00",
    unusual: true,
    aggressor: "buy",
  },
  {
    id: "3",
    symbol: "AAPL",
    expiration: "2026-06-20",
    strike: 185,
    type: "call",
    sentiment: "bullish",
    premium: 620000,
    size: 200,
    timestamp: "2026-04-05T11:30:00",
    unusual: true,
    aggressor: "buy",
  },
  {
    id: "4",
    symbol: "SPY",
    expiration: "2026-04-11",
    strike: 520,
    type: "put",
    sentiment: "bearish",
    premium: 1450000,
    size: 800,
    timestamp: "2026-04-05T13:00:00",
    unusual: true,
    aggressor: "buy",
  },
];

export interface OptionsStrategy {
  id: string;
  name: string;
  description: string;
  type: "bullish" | "bearish" | "neutral";
  riskLevel: "low" | "medium" | "high";
  maxProfit: string;
  maxLoss: string;
  legs: {
    action: "buy" | "sell";
    type: "call" | "put";
    strike: number;
    quantity: number;
  }[];
}

export const optionsStrategies: OptionsStrategy[] = [
  {
    id: "1",
    name: "Bull Call Spread",
    description: "Buy lower strike call, sell higher strike call. Limited profit and loss.",
    type: "bullish",
    riskLevel: "medium",
    maxProfit: "Limited to spread width minus net debit",
    maxLoss: "Limited to net debit paid",
    legs: [
      { action: "buy", type: "call", strike: 175, quantity: 1 },
      { action: "sell", type: "call", strike: 185, quantity: 1 },
    ],
  },
  {
    id: "2",
    name: "Iron Condor",
    description: "Sell OTM call spread and OTM put spread. Profit from low volatility.",
    type: "neutral",
    riskLevel: "medium",
    maxProfit: "Net credit received",
    maxLoss: "Spread width minus net credit",
    legs: [
      { action: "buy", type: "put", strike: 165, quantity: 1 },
      { action: "sell", type: "put", strike: 170, quantity: 1 },
      { action: "sell", type: "call", strike: 185, quantity: 1 },
      { action: "buy", type: "call", strike: 190, quantity: 1 },
    ],
  },
  {
    id: "3",
    name: "Protective Put",
    description: "Own stock and buy put for downside protection.",
    type: "bullish",
    riskLevel: "low",
    maxProfit: "Unlimited upside minus put premium",
    maxLoss: "Limited to strike price minus stock cost plus premium",
    legs: [
      { action: "buy", type: "put", strike: 170, quantity: 1 },
    ],
  },
];

export interface Alert {
  id: string;
  symbol: string;
  type: "price" | "indicator" | "volume" | "news" | "options";
  condition: string;
  targetValue: string;
  currentValue: string;
  status: "active" | "triggered" | "expired";
  createdAt: string;
  triggeredAt?: string;
}

export const alerts: Alert[] = [
  {
    id: "1",
    symbol: "AAPL",
    type: "price",
    condition: "Price above",
    targetValue: "$180.00",
    currentValue: "$178.45",
    status: "active",
    createdAt: "2026-04-01T10:00:00",
  },
  {
    id: "2",
    symbol: "NVDA",
    type: "indicator",
    condition: "RSI above",
    targetValue: "75",
    currentValue: "76.8",
    status: "triggered",
    createdAt: "2026-04-03T14:00:00",
    triggeredAt: "2026-04-05T09:30:00",
  },
  {
    id: "3",
    symbol: "TSLA",
    type: "volume",
    condition: "Volume above",
    targetValue: "100M",
    currentValue: "98.76M",
    status: "active",
    createdAt: "2026-04-04T11:00:00",
  },
  {
    id: "4",
    symbol: "MSFT",
    type: "price",
    condition: "Price below",
    targetValue: "$410.00",
    currentValue: "$412.89",
    status: "active",
    createdAt: "2026-04-02T09:00:00",
  },
];

export interface TradeJournalEntry {
  id: string;
  symbol: string;
  entryDate: string;
  exitDate?: string;
  type: "buy" | "sell";
  shares: number;
  entryPrice: number;
  exitPrice?: number;
  profitLoss?: number;
  profitLossPercent?: number;
  strategy: string;
  notes: string;
  emotions: string[];
  mistakes?: string;
  lessons?: string;
  tags: string[];
  rating: number;
}

export const tradeJournal: TradeJournalEntry[] = [
  {
    id: "1",
    symbol: "NVDA",
    entryDate: "2026-03-15T09:30:00",
    exitDate: "2026-04-02T15:45:00",
    type: "buy",
    shares: 50,
    entryPrice: 720.45,
    exitPrice: 885.23,
    profitLoss: 8239.00,
    profitLossPercent: 22.87,
    strategy: "Momentum AI",
    notes: "Strong AI chip demand, entered on bullish pattern breakout. Exit on RSI overbought.",
    emotions: ["confident", "patient"],
    lessons: "Waited for confirmation before entry. Perfect execution.",
    tags: ["AI sector", "momentum", "swing trade"],
    rating: 5,
  },
  {
    id: "2",
    symbol: "TSLA",
    entryDate: "2026-03-28T10:15:00",
    exitDate: "2026-03-30T14:20:00",
    type: "buy",
    shares: 100,
    entryPrice: 252.30,
    exitPrice: 245.78,
    profitLoss: -652.00,
    profitLossPercent: -2.58,
    strategy: "Mean Reversion",
    notes: "Thought stock was oversold, but downtrend continued. Cut losses at -3% stop.",
    emotions: ["frustrated", "impatient"],
    mistakes: "Entered too early without trend confirmation",
    lessons: "Always wait for trend reversal confirmation, not just oversold indicators.",
    tags: ["EV sector", "loss", "lesson learned"],
    rating: 2,
  },
  {
    id: "3",
    symbol: "AAPL",
    entryDate: "2026-04-01T09:35:00",
    type: "buy",
    shares: 150,
    entryPrice: 165.30,
    strategy: "Trend Following",
    notes: "Long-term position. Strong fundamentals, building position for earnings.",
    emotions: ["confident", "calm"],
    tags: ["tech", "long-term", "earnings play"],
    rating: 4,
  },
];

export interface SentimentData {
  symbol: string;
  overallScore: number;
  newsScore: number;
  socialScore: number;
  analystScore: number;
  trend: "improving" | "declining" | "stable";
  volume: number;
  mentions24h: number;
}

export const sentimentData: SentimentData[] = [
  {
    symbol: "NVDA",
    overallScore: 82,
    newsScore: 85,
    socialScore: 78,
    analystScore: 84,
    trend: "improving",
    volume: 245000,
    mentions24h: 15420,
  },
  {
    symbol: "TSLA",
    overallScore: 45,
    newsScore: 42,
    socialScore: 51,
    analystScore: 43,
    trend: "declining",
    volume: 189000,
    mentions24h: 28950,
  },
  {
    symbol: "AAPL",
    overallScore: 68,
    newsScore: 72,
    socialScore: 65,
    analystScore: 67,
    trend: "stable",
    volume: 156000,
    mentions24h: 12340,
  },
  {
    symbol: "MSFT",
    overallScore: 75,
    newsScore: 78,
    socialScore: 71,
    analystScore: 76,
    trend: "improving",
    volume: 134000,
    mentions24h: 9870,
  },
];

export interface PaperTradingAccount {
  balance: number;
  equity: number;
  buyingPower: number;
  profitLoss: number;
  profitLossPercent: number;
  totalTrades: number;
  winningTrades: number;
  losingTrades: number;
  winRate: number;
}

export const paperAccount: PaperTradingAccount = {
  balance: 100000,
  equity: 112450,
  buyingPower: 200000,
  profitLoss: 12450,
  profitLossPercent: 12.45,
  totalTrades: 47,
  winningTrades: 32,
  losingTrades: 15,
  winRate: 68.09,
};
