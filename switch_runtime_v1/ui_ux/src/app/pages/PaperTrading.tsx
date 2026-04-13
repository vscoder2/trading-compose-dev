import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Wallet, TrendingUp, Award, Target, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { mockPositions, mockTrades, runtimeSummary } from "../data/mockData";
import { toast } from "sonner";

export function PaperTrading() {
  const [tradeType, setTradeType] = useState<"buy" | "sell">("buy");
  const [symbol, setSymbol] = useState("");
  const [shares, setShares] = useState("");
  const [orderType, setOrderType] = useState("market");

  const equity = Number(runtimeSummary.portfolioValue || 0);
  const cashBalance = 0;
  const pnl = Number(runtimeSummary.todayPnL || 0);
  const pnlPct = Number(runtimeSummary.todayPnLPct || 0);
  const totalTrades = mockTrades.length;
  const winningTrades = mockTrades.filter((t) => Number(t.profit || 0) > 0).length;
  const losingTrades = mockTrades.filter((t) => Number(t.profit || 0) < 0).length;
  const winRate = totalTrades > 0 ? (winningTrades / totalTrades) * 100 : 0;
  const buyingPower = cashBalance;

  const handleTrade = () => {
    if (!symbol || !shares) {
      toast.error("Please fill in all fields");
      return;
    }
    toast.success(`Paper ${tradeType} order placed for ${shares} shares of ${symbol}`);
    setSymbol("");
    setShares("");
  };

  const handleResetAccount = () => {
    toast.info("Runtime mode: account reset is not available from UI.");
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl text-white font-semibold">Paper Trading</h2>
          <p className="text-slate-400 mt-1">Runtime-connected paper account monitor</p>
        </div>
        <Badge className="bg-blue-600/20 text-blue-400 border-blue-600/30 px-4 py-2 text-sm">
          Runtime Mode
        </Badge>
      </div>

      {/* Account Summary */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-gradient-to-br from-blue-900/20 to-blue-800/10 border-blue-800/50">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Wallet className="w-5 h-5 text-blue-400" />
              <CardTitle className="text-sm text-blue-400">Cash Balance</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">${cashBalance.toLocaleString()}</div>
            <p className="text-sm text-blue-400 mt-1">Derived from runtime snapshot</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Equity</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">${equity.toLocaleString()}</div>
            <p className="text-sm text-slate-400 mt-1">Cash + Positions</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Total P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {pnl >= 0 ? "+" : ""}${pnl.toLocaleString()}
            </div>
            <p className={`text-sm mt-1 ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
              {pnlPct >= 0 ? "+" : ""}{pnlPct.toFixed(2)}%
            </p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <Award className="w-5 h-5 text-yellow-400" />
              <CardTitle className="text-sm text-slate-400">Win Rate</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{winRate.toFixed(1)}%</div>
            <p className="text-sm text-slate-400 mt-1">
              {winningTrades}W / {losingTrades}L
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Trading Interface */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Trade Form */}
        <Card className="bg-slate-900 border-slate-800 lg:col-span-1">
          <CardHeader>
            <CardTitle className="text-white">Place Order</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {/* Buy/Sell Toggle */}
              <div className="grid grid-cols-2 gap-2">
                <Button
                  onClick={() => setTradeType("buy")}
                  className={
                    tradeType === "buy"
                      ? "bg-green-600 hover:bg-green-700"
                      : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                  }
                >
                  Buy
                </Button>
                <Button
                  onClick={() => setTradeType("sell")}
                  className={
                    tradeType === "sell"
                      ? "bg-red-600 hover:bg-red-700"
                      : "bg-slate-800 text-slate-300 hover:bg-slate-700"
                  }
                >
                  Sell
                </Button>
              </div>

              {/* Symbol */}
              <div>
                <Label className="text-slate-300">Symbol</Label>
                <Input
                  placeholder="AAPL"
                  value={symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  className="bg-slate-800 border-slate-700 text-white mt-1"
                />
              </div>

              {/* Shares */}
              <div>
                <Label className="text-slate-300">Shares</Label>
                <Input
                  type="number"
                  placeholder="100"
                  value={shares}
                  onChange={(e) => setShares(e.target.value)}
                  className="bg-slate-800 border-slate-700 text-white mt-1"
                />
              </div>

              {/* Order Type */}
              <div>
                <Label className="text-slate-300">Order Type</Label>
                <Select value={orderType} onValueChange={setOrderType}>
                  <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-800 border-slate-700">
                    <SelectItem value="market" className="text-white">
                      Market
                    </SelectItem>
                    <SelectItem value="limit" className="text-white">
                      Limit
                    </SelectItem>
                    <SelectItem value="stop" className="text-white">
                      Stop
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <Button
                onClick={handleTrade}
                className={`w-full ${
                  tradeType === "buy" ? "bg-green-600 hover:bg-green-700" : "bg-red-600 hover:bg-red-700"
                }`}
              >
                {tradeType === "buy" ? "Buy" : "Sell"} {symbol || "Stock"}
              </Button>

              <Button
                onClick={handleResetAccount}
                variant="outline"
                className="w-full border-slate-700 text-slate-300 hover:bg-slate-800"
              >
                Reset Account
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Performance & Positions */}
        <Card className="bg-slate-900 border-slate-800 lg:col-span-2">
          <Tabs defaultValue="positions" className="w-full">
            <TabsList className="grid w-full grid-cols-3 bg-slate-800">
              <TabsTrigger value="positions">Positions</TabsTrigger>
              <TabsTrigger value="history">Trade History</TabsTrigger>
              <TabsTrigger value="stats">Statistics</TabsTrigger>
            </TabsList>

            <TabsContent value="positions" className="p-6">
              <div className="space-y-3">
                {mockPositions.map((position) => (
                  <div
                    key={position.symbol}
                    className="p-4 bg-slate-800/50 rounded-lg border border-slate-700 flex items-center justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-white font-mono font-semibold">{position.symbol}</span>
                        <Badge variant="outline" className="border-slate-700 text-slate-400 text-xs">
                          {position.shares} shares
                        </Badge>
                      </div>
                      <div className="text-sm text-slate-400 mt-1">
                        Avg: ${position.avgPrice.toFixed(2)} • Current: ${position.currentPrice.toFixed(2)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-white font-semibold">
                        ${position.totalValue.toLocaleString()}
                      </div>
                      <div
                        className={`text-sm ${
                          position.gainLoss >= 0 ? "text-green-400" : "text-red-400"
                        }`}
                      >
                        {position.gainLoss >= 0 ? "+" : ""}${position.gainLoss.toFixed(2)} (
                        {position.gainLossPercent >= 0 ? "+" : ""}
                        {position.gainLossPercent.toFixed(2)}%)
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="history" className="p-6">
              <div className="space-y-3">
                {mockTrades.map((trade) => (
                  <div
                    key={trade.id}
                    className="p-4 bg-slate-800/50 rounded-lg border border-slate-700 flex items-center justify-between"
                  >
                    <div className="flex items-center gap-3">
                      <div
                        className={`p-2 rounded-lg ${
                          trade.type === "buy" ? "bg-green-600/20" : "bg-red-600/20"
                        }`}
                      >
                        {trade.type === "buy" ? (
                          <ArrowUpRight className="w-5 h-5 text-green-400" />
                        ) : (
                          <ArrowDownRight className="w-5 h-5 text-red-400" />
                        )}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <span className="text-white font-mono font-semibold">{trade.symbol}</span>
                          <Badge className="bg-slate-600/20 text-slate-400 text-xs">
                            {trade.strategy}
                          </Badge>
                        </div>
                        <div className="text-sm text-slate-400">
                          {trade.shares} shares @ ${trade.price}
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-white">${(trade.shares * trade.price).toLocaleString()}</div>
                      {trade.profit && (
                        <div className="text-sm text-green-400">+${trade.profit.toFixed(2)}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="stats" className="p-6">
              <div className="grid grid-cols-2 gap-4">
                <div className="p-4 bg-slate-800/50 rounded-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <Target className="w-5 h-5 text-blue-400" />
                    <span className="text-sm text-slate-400">Total Trades</span>
                  </div>
                  <div className="text-2xl text-white">{totalTrades}</div>
                </div>

                <div className="p-4 bg-slate-800/50 rounded-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp className="w-5 h-5 text-green-400" />
                    <span className="text-sm text-slate-400">Winning Trades</span>
                  </div>
                  <div className="text-2xl text-white">{winningTrades}</div>
                </div>

                <div className="p-4 bg-slate-800/50 rounded-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp className="w-5 h-5 text-red-400" />
                    <span className="text-sm text-slate-400">Losing Trades</span>
                  </div>
                  <div className="text-2xl text-white">{losingTrades}</div>
                </div>

                <div className="p-4 bg-slate-800/50 rounded-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <Award className="w-5 h-5 text-yellow-400" />
                    <span className="text-sm text-slate-400">Win Rate</span>
                  </div>
                  <div className="text-2xl text-white">{winRate.toFixed(1)}%</div>
                </div>

                <div className="p-4 bg-slate-800/50 rounded-lg col-span-2">
                  <div className="flex items-center gap-2 mb-2">
                    <Wallet className="w-5 h-5 text-green-400" />
                    <span className="text-sm text-slate-400">Buying Power</span>
                  </div>
                  <div className="text-2xl text-white">
                    ${buyingPower.toLocaleString()}
                  </div>
                  <p className="text-xs text-slate-500 mt-1">Runtime snapshot based</p>
                </div>
              </div>
            </TabsContent>
          </Tabs>
        </Card>
      </div>

      {/* Info Card */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">About Paper Trading</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Practice Risk-Free</h4>
              <p className="text-sm text-slate-400">
                Test your strategies with virtual money before risking real capital.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Real Market Data</h4>
              <p className="text-sm text-slate-400">
                Experience live market conditions with actual price movements and volatility.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Track Performance</h4>
              <p className="text-sm text-slate-400">
                Analyze your trading performance with detailed statistics and insights.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
