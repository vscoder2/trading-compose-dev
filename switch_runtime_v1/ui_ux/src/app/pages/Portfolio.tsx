import { TrendingUp, TrendingDown, PieChart as PieChartIcon } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip, AreaChart, Area, XAxis, YAxis, CartesianGrid } from "recharts";
import { mockPositions, generatePriceData } from "../data/mockData";
import { useState } from "react";

export function Portfolio() {
  const [selectedStock, setSelectedStock] = useState(mockPositions[0]?.symbol || "");
  const priceData = generatePriceData(selectedStock);
  
  const totalValue = mockPositions.reduce((sum, pos) => sum + pos.totalValue, 0);
  const totalGainLoss = mockPositions.reduce((sum, pos) => sum + pos.gainLoss, 0);
  const denom = totalValue - totalGainLoss;
  const totalGainLossPercent = denom > 0 ? (totalGainLoss / denom) * 100 : 0;

  const portfolioDistribution = mockPositions.map((pos) => ({
    name: pos.symbol,
    value: totalValue > 0 ? parseFloat(((pos.totalValue / totalValue) * 100).toFixed(2)) : 0,
  }));

  const COLORS = ["#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981", "#06b6d4"];

  return (
    <div className="space-y-6">
      {/* Portfolio Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Value</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">${totalValue.toLocaleString()}</div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Gain/Loss</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-3xl ${totalGainLoss >= 0 ? "text-green-500" : "text-red-500"}`}>
              {totalGainLoss >= 0 ? "+" : ""}${totalGainLoss.toLocaleString()}
            </div>
            <div className={`flex items-center gap-1 mt-1 ${totalGainLoss >= 0 ? "text-green-500" : "text-red-500"}`}>
              {totalGainLoss >= 0 ? (
                <TrendingUp className="w-4 h-4" />
              ) : (
                <TrendingDown className="w-4 h-4" />
              )}
              <span className="text-sm">{totalGainLossPercent.toFixed(2)}%</span>
            </div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Positions</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{mockPositions.length}</div>
            <p className="text-sm text-slate-400 mt-1">Active holdings</p>
          </CardContent>
        </Card>
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Portfolio Distribution */}
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <div className="flex items-center gap-2">
              <PieChartIcon className="w-5 h-5 text-blue-400" />
              <CardTitle className="text-white">Portfolio Distribution</CardTitle>
            </div>
            <p className="text-sm text-slate-400">Allocation by stock</p>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <PieChart>
                <Pie
                  data={portfolioDistribution}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={(entry) => `${entry.name} ${entry.value}%`}
                  outerRadius={100}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {portfolioDistribution.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: "8px",
                    color: "#fff",
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        {/* Stock Price Chart */}
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <CardTitle className="text-white">Price History</CardTitle>
            <div className="flex gap-2 mt-2">
              {mockPositions.map((pos) => (
                <button
                  key={pos.symbol}
                  onClick={() => setSelectedStock(pos.symbol)}
                  className={`px-3 py-1 rounded text-sm transition-colors ${
                    selectedStock === pos.symbol
                      ? "bg-blue-600 text-white"
                      : "bg-slate-800 text-slate-400 hover:bg-slate-700"
                  }`}
                >
                  {pos.symbol}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <AreaChart data={priceData}>
                <defs>
                  <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="time" stroke="#64748b" />
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
                  dataKey="price"
                  stroke="#3b82f6"
                  strokeWidth={2}
                  fill="url(#colorPrice)"
                />
              </AreaChart>
            </ResponsiveContainer>
            {priceData.length === 0 && (
              <p className="mt-3 text-sm text-slate-500">No runtime price points for selected symbol.</p>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Holdings Table */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Holdings</CardTitle>
          <p className="text-sm text-slate-400">Your current positions</p>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-800">
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Symbol</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Name</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Shares</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Avg Price</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Current Price</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Total Value</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Gain/Loss</th>
                </tr>
              </thead>
              <tbody>
                {mockPositions.map((position) => (
                  <tr key={position.symbol} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="py-4 px-4">
                      <span className="text-white font-mono font-semibold">{position.symbol}</span>
                    </td>
                    <td className="py-4 px-4 text-slate-300">{position.name}</td>
                    <td className="py-4 px-4 text-right text-white">{position.shares}</td>
                    <td className="py-4 px-4 text-right text-slate-400">${position.avgPrice.toFixed(2)}</td>
                    <td className="py-4 px-4 text-right text-white">${position.currentPrice.toFixed(2)}</td>
                    <td className="py-4 px-4 text-right text-white">${position.totalValue.toLocaleString()}</td>
                    <td className="py-4 px-4 text-right">
                      <div className={position.gainLoss >= 0 ? "text-green-500" : "text-red-500"}>
                        <div className="font-medium">
                          {position.gainLoss >= 0 ? "+" : ""}${position.gainLoss.toLocaleString()}
                        </div>
                        <div className="text-sm">
                          {position.gainLoss >= 0 ? "+" : ""}{position.gainLossPercent.toFixed(2)}%
                        </div>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
