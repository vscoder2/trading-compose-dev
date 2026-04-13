import { ArrowUpRight, ArrowDownRight, TrendingUp, DollarSign, Activity, Bot } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { mockStocks, generateChartData, mockBots, mockTrades, runtimeSummary } from "../data/mockData";
import { Watchlist } from "../components/Watchlist";
import { TradeExecutor } from "../components/TradeExecutor";
import { NewsFeed } from "../components/NewsFeed";

export function Dashboard() {
  const portfolioData = generateChartData(30);
  const totalPortfolioValue = runtimeSummary.portfolioValue;
  const todayGainLoss = runtimeSummary.todayPnL;
  const todayGainLossPercent = runtimeSummary.todayPnLPct;
  const activeBots = runtimeSummary.activeBots || mockBots.filter(bot => bot.status === "active").length;
  const totalProfit = runtimeSummary.totalBotProfit || mockBots.reduce((sum, bot) => sum + bot.profit, 0);

  return (
    <div className="space-y-6">
      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm text-slate-400">Portfolio Value</CardTitle>
            <DollarSign className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">${totalPortfolioValue.toLocaleString()}</div>
            <div className="flex items-center gap-1 mt-1">
              <ArrowUpRight className="w-4 h-4 text-green-500" />
              <span className="text-sm text-green-500">
                +${todayGainLoss.toLocaleString()} ({todayGainLossPercent}%)
              </span>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm text-slate-400">AI Bot Profit</CardTitle>
            <Bot className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">${totalProfit.toLocaleString()}</div>
            <p className="text-sm text-slate-400 mt-1">{activeBots} bots active</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm text-slate-400">Today's Trades</CardTitle>
            <Activity className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{runtimeSummary.todaysTrades || mockTrades.length}</div>
            <p className="text-sm text-slate-400 mt-1">runtime event-driven</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-sm text-slate-400">Win Rate</CardTitle>
            <TrendingUp className="w-4 h-4 text-slate-400" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">63.8%</div>
            <p className="text-sm text-slate-400 mt-1">Last 30 days</p>
          </CardContent>
        </Card>
      </div>

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left Column - Charts */}
        <div className="lg:col-span-2 space-y-6">
          {/* Portfolio Performance */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="text-white">Portfolio Performance</CardTitle>
              <p className="text-sm text-slate-400">Last 30 days</p>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={300}>
                <AreaChart data={portfolioData}>
                  <defs>
                    <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="date" stroke="#64748b" />
                  <YAxis stroke="#64748b" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #334155",
                      borderRadius: "8px",
                      color: "#fff",
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fill="url(#colorValue)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {/* Active Bots Performance */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="text-white">AI Trading Bots</CardTitle>
              <p className="text-sm text-slate-400">Performance overview</p>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {mockBots.filter(bot => bot.status === "active").map((bot) => (
                  <div key={bot.id} className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <Bot className="w-4 h-4 text-blue-400" />
                        <span className="text-white">{bot.name}</span>
                      </div>
                      <p className="text-xs text-slate-400 mt-1">{bot.totalTrades} trades • {bot.winRate}% win rate</p>
                    </div>
                    <div className="text-right">
                      <div className="text-sm text-green-400">+${bot.profit.toLocaleString()}</div>
                      <div className={`text-xs px-2 py-1 rounded mt-1 ${
                        bot.riskLevel === "low" ? "bg-green-600/20 text-green-400" :
                        bot.riskLevel === "medium" ? "bg-yellow-600/20 text-yellow-400" :
                        "bg-red-600/20 text-red-400"
                      }`}>
                        {bot.riskLevel} risk
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>

          {/* Market News */}
          <NewsFeed />
        </div>

        {/* Right Column - Tools */}
        <div className="space-y-6">
          <TradeExecutor />
          <Watchlist />
        </div>
      </div>

      {/* Market Overview */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Market Overview</CardTitle>
          <p className="text-sm text-slate-400">Top stocks being tracked</p>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-800">
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Symbol</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Name</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Price</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Change</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Volume</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Market Cap</th>
                </tr>
              </thead>
              <tbody>
                {mockStocks.map((stock) => (
                  <tr key={stock.symbol} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="py-3 px-4">
                      <span className="text-white font-mono">{stock.symbol}</span>
                    </td>
                    <td className="py-3 px-4 text-slate-300">{stock.name}</td>
                    <td className="py-3 px-4 text-right text-white">${stock.price.toFixed(2)}</td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {stock.change >= 0 ? (
                          <>
                            <ArrowUpRight className="w-4 h-4 text-green-500" />
                            <span className="text-green-500">
                              +${Math.abs(stock.change).toFixed(2)} ({stock.changePercent}%)
                            </span>
                          </>
                        ) : (
                          <>
                            <ArrowDownRight className="w-4 h-4 text-red-500" />
                            <span className="text-red-500">
                              -${Math.abs(stock.change).toFixed(2)} ({Math.abs(stock.changePercent)}%)
                            </span>
                          </>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-right text-slate-400">
                      {(stock.volume / 1000000).toFixed(1)}M
                    </td>
                    <td className="py-3 px-4 text-right text-slate-400">{stock.marketCap}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Recent Trades */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Recent Trades</CardTitle>
          <p className="text-sm text-slate-400">Latest automated trades</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {mockTrades.slice(0, 5).map((trade) => (
              <div key={trade.id} className="flex items-center justify-between p-3 bg-slate-800/30 rounded-lg">
                <div className="flex items-center gap-4">
                  <div className={`px-3 py-1 rounded text-sm ${
                    trade.type === "buy" ? "bg-green-600/20 text-green-400" : "bg-red-600/20 text-red-400"
                  }`}>
                    {trade.type.toUpperCase()}
                  </div>
                  <div>
                    <div className="text-white font-mono">{trade.symbol}</div>
                    <div className="text-xs text-slate-400">{trade.strategy}</div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-white">{trade.shares} shares @ ${trade.price}</div>
                  <div className="text-xs text-slate-400">
                    {new Date(trade.timestamp).toLocaleTimeString()}
                  </div>
                </div>
                {trade.profit && (
                  <div className="text-green-400 font-medium">+${trade.profit.toLocaleString()}</div>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
