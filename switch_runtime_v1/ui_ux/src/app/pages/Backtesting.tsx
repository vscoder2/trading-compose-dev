import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Play, Settings } from "lucide-react";
import { generateChartData, mockBots, mockTrades, runtimeSummary } from "../data/mockData";
import { toast } from "sonner";

export function Backtesting() {
  const [isRunning, setIsRunning] = useState(false);
  const [selectedStrategy, setSelectedStrategy] = useState(mockBots[0]?.id || "runtime-1");
  const [startDate, setStartDate] = useState("2025-01-01");
  const [endDate, setEndDate] = useState("2026-04-04");
  const [initialCapital, setInitialCapital] = useState("100000");
  const [results, setResults] = useState<any>(null);

  const runBacktest = () => {
    setIsRunning(true);

    setTimeout(() => {
      const init = Number(initialCapital || 0);
      const finalValue = Number(runtimeSummary.portfolioValue || init);
      const totalReturn = finalValue - init;
      const returnPercent = init > 0 ? (totalReturn / init) * 100 : 0;
      const winning = mockTrades.filter((t) => Number(t.profit || 0) > 0).length;
      const losing = mockTrades.filter((t) => Number(t.profit || 0) < 0).length;
      const winRate = mockTrades.length > 0 ? (winning / mockTrades.length) * 100 : 0;
      const posProfits = mockTrades.map((t) => Number(t.profit || 0)).filter((p) => p > 0);
      const negProfits = mockTrades.map((t) => Number(t.profit || 0)).filter((p) => p < 0);
      const avgWin = posProfits.length ? posProfits.reduce((a, b) => a + b, 0) / posProfits.length : 0;
      const avgLoss = negProfits.length ? negProfits.reduce((a, b) => a + b, 0) / negProfits.length : 0;
      const grossProfit = posProfits.reduce((a, b) => a + b, 0);
      const grossLoss = Math.abs(negProfits.reduce((a, b) => a + b, 0));
      const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : 0;
      const maxDrawdown = -Math.abs(Number(runtimeSummary.maxDrawdownPct || 0));

      const mockResults = {
        finalValue,
        totalReturn,
        returnPercent,
        totalTrades: mockTrades.length,
        winningTrades: winning,
        losingTrades: losing,
        winRate,
        sharpeRatio: 0,
        maxDrawdown,
        avgWin,
        avgLoss,
        profitFactor,
        chartData: generateBacktestData(),
      };
      
      setResults(mockResults);
      setIsRunning(false);
      toast.success("Runtime backtest snapshot loaded");
    }, 250);
  };

  const generateBacktestData = () => {
    const curve = generateChartData(120);
    if (!curve.length) return [];
    const init = Number(initialCapital || 0);
    return curve.map((row, idx) => ({
      day: idx,
      portfolio: Number(row.value || 0),
      benchmark: init,
    }));
  };

  const selectedBot = mockBots.find(bot => bot.id === selectedStrategy);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl text-white font-semibold">Strategy Backtesting</h2>
          <p className="text-slate-400 mt-1">Test your strategies against historical data</p>
        </div>
      </div>

      {/* Configuration */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Settings className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Backtest Configuration</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <Label className="text-slate-300">Strategy</Label>
              <Select value={selectedStrategy} onValueChange={setSelectedStrategy}>
                <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-slate-800 border-slate-700">
                  {mockBots.map((bot) => (
                    <SelectItem key={bot.id} value={bot.id} className="text-white">
                      {bot.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div>
              <Label className="text-slate-300">Start Date</Label>
              <Input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>

            <div>
              <Label className="text-slate-300">End Date</Label>
              <Input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>

            <div>
              <Label className="text-slate-300">Initial Capital</Label>
              <Input
                type="number"
                value={initialCapital}
                onChange={(e) => setInitialCapital(e.target.value)}
                className="bg-slate-800 border-slate-700 text-white mt-1"
                placeholder="100000"
              />
            </div>
          </div>

          {selectedBot && (
            <div className="mt-4 p-4 bg-slate-800/50 rounded-lg border border-slate-700">
              <p className="text-sm text-slate-400">{selectedBot.strategy}</p>
              <div className="flex gap-4 mt-2 text-sm">
                <span className="text-slate-300">Risk: <span className="text-white">{selectedBot.riskLevel}</span></span>
                <span className="text-slate-300">Current Win Rate: <span className="text-white">{selectedBot.winRate}%</span></span>
              </div>
            </div>
          )}

          <Button
            onClick={runBacktest}
            disabled={isRunning}
            className="mt-4 bg-blue-600 hover:bg-blue-700"
          >
            <Play className="w-4 h-4 mr-2" />
            {isRunning ? "Running Backtest..." : "Run Backtest"}
          </Button>
        </CardContent>
      </Card>

      {/* Results */}
      {results && (
        <>
          {/* Metrics Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <Card className="bg-slate-900 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-400">Final Value</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl text-white">${results.finalValue.toLocaleString()}</div>
                <div className="flex items-center gap-1 mt-1">
                  <TrendingUp className="w-4 h-4 text-green-500" />
                  <span className="text-sm text-green-500">
                    +${results.totalReturn.toLocaleString()} ({results.returnPercent}%)
                  </span>
                </div>
              </CardContent>
            </Card>

            <Card className="bg-slate-900 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-400">Win Rate</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl text-white">{results.winRate}%</div>
                <p className="text-sm text-slate-400 mt-1">
                  {results.winningTrades}W / {results.losingTrades}L
                </p>
              </CardContent>
            </Card>

            <Card className="bg-slate-900 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-400">Sharpe Ratio</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl text-white">{results.sharpeRatio}</div>
                <p className="text-sm text-slate-400 mt-1">Risk-adjusted return</p>
              </CardContent>
            </Card>

            <Card className="bg-slate-900 border-slate-800">
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-400">Max Drawdown</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl text-red-400">{results.maxDrawdown}%</div>
                <p className="text-sm text-slate-400 mt-1">Peak to trough</p>
              </CardContent>
            </Card>
          </div>

          {/* Performance Chart */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="text-white">Performance Comparison</CardTitle>
              <p className="text-sm text-slate-400">Strategy vs Benchmark</p>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={400}>
                <LineChart data={results.chartData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                  <XAxis dataKey="day" stroke="#64748b" label={{ value: "Days", position: "insideBottom", offset: -5 }} />
                  <YAxis stroke="#64748b" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#1e293b",
                      border: "1px solid #334155",
                      borderRadius: "8px",
                      color: "#fff",
                    }}
                  />
                  <Legend />
                  <Line
                    type="monotone"
                    dataKey="portfolio"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    name="Strategy"
                    dot={false}
                  />
                  <Line
                    type="monotone"
                    dataKey="benchmark"
                    stroke="#64748b"
                    strokeWidth={2}
                    strokeDasharray="5 5"
                    name="Benchmark"
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          {/* Detailed Metrics */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card className="bg-slate-900 border-slate-800">
              <CardHeader>
                <CardTitle className="text-white">Trade Statistics</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Total Trades</span>
                    <span className="text-white font-semibold">{results.totalTrades}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Winning Trades</span>
                    <span className="text-green-400 font-semibold">{results.winningTrades}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Losing Trades</span>
                    <span className="text-red-400 font-semibold">{results.losingTrades}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Avg Win</span>
                    <span className="text-green-400 font-semibold">+${results.avgWin}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Avg Loss</span>
                    <span className="text-red-400 font-semibold">${results.avgLoss}</span>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="bg-slate-900 border-slate-800">
              <CardHeader>
                <CardTitle className="text-white">Risk Metrics</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Profit Factor</span>
                    <span className="text-white font-semibold">{results.profitFactor}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Sharpe Ratio</span>
                    <span className="text-white font-semibold">{results.sharpeRatio}</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Max Drawdown</span>
                    <span className="text-red-400 font-semibold">{results.maxDrawdown}%</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Win Rate</span>
                    <span className="text-white font-semibold">{results.winRate}%</span>
                  </div>
                  <div className="flex items-center justify-between p-3 bg-slate-800/50 rounded-lg">
                    <span className="text-slate-400">Return %</span>
                    <span className="text-green-400 font-semibold">+{results.returnPercent}%</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
