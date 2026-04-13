import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Target } from "lucide-react";
import { mockBots, mockTrades } from "../data/mockData";

function _safeStrategy(v: string | undefined) {
  return (v || "runtime").trim() || "runtime";
}

export function Analytics() {
  const grouped = new Map<string, { trades: number; profit: number; wins: number }>();
  for (const t of mockTrades) {
    const key = _safeStrategy(t.strategy);
    const cur = grouped.get(key) || { trades: 0, profit: 0, wins: 0 };
    const p = Number(t.profit || 0);
    cur.trades += 1;
    cur.profit += p;
    if (p > 0) cur.wins += 1;
    grouped.set(key, cur);
  }

  const strategyPerformance = Array.from(grouped.entries()).map(([name, v]) => ({
    name,
    trades: v.trades,
    profit: v.profit,
    winRate: v.trades > 0 ? (v.wins / v.trades) * 100 : 0,
  }));

  const totalProfit = strategyPerformance.reduce((sum, s) => sum + s.profit, 0);
  const totalTrades = strategyPerformance.reduce((sum, s) => sum + s.trades, 0);
  const avgWinRate = strategyPerformance.length
    ? strategyPerformance.reduce((sum, s) => sum + s.winRate, 0) / strategyPerformance.length
    : 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Profit</CardTitle>
          </CardHeader>
          <CardContent>
            <div className={`text-3xl ${totalProfit >= 0 ? "text-green-500" : "text-red-500"}`}>
              {totalProfit >= 0 ? "+" : ""}${totalProfit.toLocaleString()}
            </div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Trades</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{totalTrades.toLocaleString()}</div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Avg Win Rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{avgWinRate.toFixed(1)}%</div>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Runtime Bots</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{mockBots.length}</div>
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Target className="w-5 h-5 text-blue-400" />
              <CardTitle className="text-white">Strategy Profit (Runtime)</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={strategyPerformance}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="name" stroke="#64748b" />
                <YAxis stroke="#64748b" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: "8px",
                    color: "#fff",
                  }}
                />
                <Bar dataKey="profit" fill="#3b82f6" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <CardTitle className="text-white">Strategy Details</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-slate-800">
                    <th className="text-left py-3 px-4 text-sm text-slate-400">Strategy</th>
                    <th className="text-right py-3 px-4 text-sm text-slate-400">Trades</th>
                    <th className="text-right py-3 px-4 text-sm text-slate-400">Win Rate</th>
                    <th className="text-right py-3 px-4 text-sm text-slate-400">Profit</th>
                  </tr>
                </thead>
                <tbody>
                  {strategyPerformance.map((strategy) => (
                    <tr key={strategy.name} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="py-4 px-4 text-white font-medium">{strategy.name}</td>
                      <td className="py-4 px-4 text-right text-slate-300">{strategy.trades}</td>
                      <td className="py-4 px-4 text-right text-slate-300">{strategy.winRate.toFixed(1)}%</td>
                      <td className={`py-4 px-4 text-right ${strategy.profit >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {strategy.profit >= 0 ? "+" : ""}${strategy.profit.toLocaleString()}
                      </td>
                    </tr>
                  ))}
                  {strategyPerformance.length === 0 && (
                    <tr>
                      <td colSpan={4} className="py-8 text-center text-slate-500">
                        No runtime trade data found.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

