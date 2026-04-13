// Advanced market data for professional features

export interface ScreenerCriteria {
  priceMin?: number;
  priceMax?: number;
  marketCapMin?: string;
  marketCapMax?: string;
  volumeMin?: number;
  peRatioMax?: number;
  dividendYieldMin?: number;
  changePercentMin?: number;
  rsiMin?: number;
  rsiMax?: number;
  sector?: string;
}

export interface ScreenerStock {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePercent: number;
  volume: number;
  marketCap: string;
  peRatio: number;
  dividendYield: number;
  rsi: number;
  macd: "bullish" | "bearish" | "neutral";
  sector: string;
  fiftyTwoWeekHigh: number;
  fiftyTwoWeekLow: number;
}

export const screenerStocks: ScreenerStock[] = [
  {
    symbol: "AAPL",
    name: "Apple Inc.",
    price: 178.45,
    change: 2.34,
    changePercent: 1.33,
    volume: 52340000,
    marketCap: "2.8T",
    peRatio: 28.5,
    dividendYield: 0.52,
    rsi: 64.2,
    macd: "bullish",
    sector: "Technology",
    fiftyTwoWeekHigh: 199.62,
    fiftyTwoWeekLow: 164.08,
  },
  {
    symbol: "MSFT",
    name: "Microsoft Corp.",
    price: 412.89,
    change: -1.23,
    changePercent: -0.30,
    volume: 28450000,
    marketCap: "3.1T",
    peRatio: 35.2,
    dividendYield: 0.72,
    rsi: 58.7,
    macd: "neutral",
    sector: "Technology",
    fiftyTwoWeekHigh: 430.82,
    fiftyTwoWeekLow: 309.45,
  },
  {
    symbol: "GOOGL",
    name: "Alphabet Inc.",
    price: 142.67,
    change: 3.45,
    changePercent: 2.48,
    volume: 31250000,
    marketCap: "1.8T",
    peRatio: 24.8,
    dividendYield: 0.00,
    rsi: 71.3,
    macd: "bullish",
    sector: "Technology",
    fiftyTwoWeekHigh: 155.30,
    fiftyTwoWeekLow: 121.46,
  },
  {
    symbol: "NVDA",
    name: "NVIDIA Corp.",
    price: 885.23,
    change: 12.67,
    changePercent: 1.45,
    volume: 45780000,
    marketCap: "2.2T",
    peRatio: 71.5,
    dividendYield: 0.03,
    rsi: 76.8,
    macd: "bullish",
    sector: "Technology",
    fiftyTwoWeekHigh: 974.00,
    fiftyTwoWeekLow: 410.22,
  },
  {
    symbol: "JPM",
    name: "JPMorgan Chase",
    price: 198.45,
    change: 1.23,
    changePercent: 0.62,
    volume: 12340000,
    marketCap: "570B",
    peRatio: 11.2,
    dividendYield: 2.35,
    rsi: 55.4,
    macd: "neutral",
    sector: "Financial",
    fiftyTwoWeekHigh: 208.50,
    fiftyTwoWeekLow: 135.19,
  },
  {
    symbol: "JNJ",
    name: "Johnson & Johnson",
    price: 162.34,
    change: -0.45,
    changePercent: -0.28,
    volume: 8750000,
    marketCap: "390B",
    peRatio: 15.8,
    dividendYield: 3.12,
    rsi: 48.9,
    macd: "bearish",
    sector: "Healthcare",
    fiftyTwoWeekHigh: 179.92,
    fiftyTwoWeekLow: 143.13,
  },
  {
    symbol: "XOM",
    name: "Exxon Mobil",
    price: 114.67,
    change: 2.89,
    changePercent: 2.59,
    volume: 18920000,
    marketCap: "470B",
    peRatio: 12.4,
    dividendYield: 3.48,
    rsi: 62.1,
    macd: "bullish",
    sector: "Energy",
    fiftyTwoWeekHigh: 123.75,
    fiftyTwoWeekLow: 95.63,
  },
  {
    symbol: "WMT",
    name: "Walmart Inc.",
    price: 168.92,
    change: 0.78,
    changePercent: 0.46,
    volume: 9340000,
    marketCap: "450B",
    peRatio: 28.9,
    dividendYield: 1.34,
    rsi: 52.7,
    macd: "neutral",
    sector: "Consumer",
    fiftyTwoWeekHigh: 175.43,
    fiftyTwoWeekLow: 142.05,
  },
];

export interface AIInsight {
  id: string;
  type: "prediction" | "pattern" | "anomaly" | "opportunity";
  title: string;
  description: string;
  confidence: number;
  timeframe: string;
  relatedSymbols: string[];
  action: "buy" | "sell" | "hold";
  targetPrice?: number;
  currentPrice?: number;
  potentialGain?: number;
  timestamp: string;
}

export const aiInsights: AIInsight[] = [
  {
    id: "1",
    type: "prediction",
    title: "NVDA Breakout Pattern Detected",
    description: "AI model identifies cup and handle formation with 84% historical accuracy. Expected breakout above $900 within 3-5 trading days.",
    confidence: 84,
    timeframe: "3-5 days",
    relatedSymbols: ["NVDA"],
    action: "buy",
    targetPrice: 925,
    currentPrice: 885.23,
    potentialGain: 4.49,
    timestamp: "2026-04-05T08:30:00",
  },
  {
    id: "2",
    type: "opportunity",
    title: "Tech Sector Momentum Building",
    description: "Machine learning analysis shows strong institutional buying across semiconductor stocks. Correlation patterns suggest sector-wide rally.",
    confidence: 76,
    timeframe: "1-2 weeks",
    relatedSymbols: ["NVDA", "AMD", "INTC"],
    action: "buy",
    timestamp: "2026-04-05T09:15:00",
  },
  {
    id: "3",
    type: "anomaly",
    title: "TSLA Volume Spike Detected",
    description: "Unusual trading volume 3.2x above average. Historical data suggests this pattern precedes significant price movement.",
    confidence: 71,
    timeframe: "24-48 hours",
    relatedSymbols: ["TSLA"],
    action: "hold",
    timestamp: "2026-04-05T10:00:00",
  },
  {
    id: "4",
    type: "pattern",
    title: "Mean Reversion Signal: AAPL",
    description: "Stock trading 2.1 standard deviations below 20-day moving average. AI probability model suggests 68% chance of upward correction.",
    confidence: 68,
    timeframe: "5-7 days",
    relatedSymbols: ["AAPL"],
    action: "buy",
    targetPrice: 185,
    currentPrice: 178.45,
    potentialGain: 3.67,
    timestamp: "2026-04-05T11:30:00",
  },
  {
    id: "5",
    type: "prediction",
    title: "Market Sentiment Shift Warning",
    description: "Natural language processing of financial news shows sentiment turning negative. VIX patterns suggest increased volatility ahead.",
    confidence: 82,
    timeframe: "2-3 days",
    relatedSymbols: ["SPY", "QQQ"],
    action: "sell",
    timestamp: "2026-04-05T07:45:00",
  },
];

export interface EconomicEvent {
  id: string;
  date: string;
  time: string;
  event: string;
  currency: string;
  impact: "high" | "medium" | "low";
  forecast?: string;
  previous?: string;
  actual?: string;
}

export const economicCalendar: EconomicEvent[] = [
  {
    id: "1",
    date: "2026-04-05",
    time: "08:30",
    event: "Non-Farm Payrolls",
    currency: "USD",
    impact: "high",
    forecast: "185K",
    previous: "275K",
  },
  {
    id: "2",
    date: "2026-04-05",
    time: "10:00",
    event: "ISM Services PMI",
    currency: "USD",
    impact: "high",
    forecast: "52.6",
    previous: "52.6",
  },
  {
    id: "3",
    date: "2026-04-06",
    time: "14:00",
    event: "Fed Chair Powell Speech",
    currency: "USD",
    impact: "high",
  },
  {
    id: "4",
    date: "2026-04-07",
    time: "08:30",
    event: "Consumer Price Index",
    currency: "USD",
    impact: "high",
    forecast: "3.4%",
    previous: "3.2%",
  },
  {
    id: "5",
    date: "2026-04-08",
    time: "09:00",
    event: "AAPL Earnings Report",
    currency: "USD",
    impact: "high",
    forecast: "$1.52",
    previous: "$1.46",
  },
  {
    id: "6",
    date: "2026-04-08",
    time: "After Market",
    event: "NVDA Earnings Report",
    currency: "USD",
    impact: "high",
    forecast: "$5.20",
    previous: "$4.93",
  },
];

export interface Trader {
  id: string;
  name: string;
  avatar: string;
  rank: number;
  totalReturn: number;
  monthlyReturn: number;
  winRate: number;
  totalTrades: number;
  followers: number;
  copiers: number;
  riskScore: number;
  strategy: string;
  verified: boolean;
}

export const topTraders: Trader[] = [
  {
    id: "1",
    name: "Alex Chen",
    avatar: "AC",
    rank: 1,
    totalReturn: 187.4,
    monthlyReturn: 12.8,
    winRate: 73.2,
    totalTrades: 892,
    followers: 15420,
    copiers: 3240,
    riskScore: 6.5,
    strategy: "AI-Powered Momentum",
    verified: true,
  },
  {
    id: "2",
    name: "Sarah Miller",
    avatar: "SM",
    rank: 2,
    totalReturn: 164.9,
    monthlyReturn: 10.2,
    winRate: 68.7,
    totalTrades: 1247,
    followers: 12380,
    copiers: 2890,
    riskScore: 5.2,
    strategy: "Swing Trading",
    verified: true,
  },
  {
    id: "3",
    name: "James Rodriguez",
    avatar: "JR",
    rank: 3,
    totalReturn: 152.3,
    monthlyReturn: 9.8,
    winRate: 71.5,
    totalTrades: 623,
    followers: 10920,
    copiers: 2340,
    riskScore: 7.1,
    strategy: "Tech Sector Focus",
    verified: true,
  },
  {
    id: "4",
    name: "Emma Watson",
    avatar: "EW",
    rank: 4,
    totalReturn: 143.7,
    monthlyReturn: 8.9,
    winRate: 65.3,
    totalTrades: 1456,
    followers: 9840,
    copiers: 2120,
    riskScore: 4.8,
    strategy: "Dividend Growth",
    verified: true,
  },
  {
    id: "5",
    name: "Michael Park",
    avatar: "MP",
    rank: 5,
    totalReturn: 138.2,
    monthlyReturn: 8.4,
    winRate: 69.8,
    totalTrades: 734,
    followers: 8560,
    copiers: 1890,
    riskScore: 6.0,
    strategy: "Options Strategies",
    verified: false,
  },
];

export const sectorPerformance = [
  { sector: "Technology", performance: 12.4, color: "#3b82f6" },
  { sector: "Healthcare", performance: 8.7, color: "#10b981" },
  { sector: "Financial", performance: 6.2, color: "#f59e0b" },
  { sector: "Energy", performance: 15.9, color: "#ef4444" },
  { sector: "Consumer", performance: 4.3, color: "#8b5cf6" },
  { sector: "Industrial", performance: 7.8, color: "#06b6d4" },
  { sector: "Materials", performance: 9.1, color: "#ec4899" },
  { sector: "Utilities", performance: 2.5, color: "#14b8a6" },
];

export interface OrderHistory {
  id: string;
  symbol: string;
  type: "buy" | "sell";
  orderType: "market" | "limit" | "stop";
  shares: number;
  price: number;
  total: number;
  status: "filled" | "pending" | "cancelled" | "partial";
  timestamp: string;
  fillRate?: number;
}

export const orderHistory: OrderHistory[] = [
  {
    id: "ORD-001",
    symbol: "AAPL",
    type: "buy",
    orderType: "limit",
    shares: 50,
    price: 175.30,
    total: 8765.00,
    status: "filled",
    timestamp: "2026-04-04T09:35:00",
    fillRate: 100,
  },
  {
    id: "ORD-002",
    symbol: "NVDA",
    type: "sell",
    orderType: "market",
    shares: 25,
    price: 890.45,
    total: 22261.25,
    status: "filled",
    timestamp: "2026-04-04T10:22:00",
    fillRate: 100,
  },
  {
    id: "ORD-003",
    symbol: "MSFT",
    type: "buy",
    orderType: "stop",
    shares: 30,
    price: 410.00,
    total: 12300.00,
    status: "pending",
    timestamp: "2026-04-04T11:00:00",
  },
  {
    id: "ORD-004",
    symbol: "GOOGL",
    type: "buy",
    orderType: "limit",
    shares: 75,
    price: 140.25,
    total: 10518.75,
    status: "partial",
    timestamp: "2026-04-04T13:45:00",
    fillRate: 60,
  },
];
