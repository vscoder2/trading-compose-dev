import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { TrendingUp, TrendingDown } from "lucide-react";
import { sectorPerformance, screenerStocks } from "../data/advancedData";

export function MarketHeatmap() {
  const stocksWithSize = screenerStocks.map(stock => {
    const marketCapValue = parseFloat(stock.marketCap.replace(/[TB]/g, ""));
    const multiplier = stock.marketCap.includes("T") ? 1000 : 1;
    return {
      ...stock,
      size: marketCapValue * multiplier,
    };
  });

  const getSectorColor = (sector: string) => {
    const colors: Record<string, string> = {
      Technology: "#3b82f6",
      Financial: "#f59e0b",
      Healthcare: "#10b981",
      Energy: "#ef4444",
      Consumer: "#8b5cf6",
    };
    return colors[sector] || "#64748b";
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Market Heatmap</h2>
        <p className="text-slate-400 mt-1">Visual overview of market performance</p>
      </div>

      {/* Market Summary */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Gainers</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {screenerStocks.filter(s => s.changePercent > 0).length}
            </div>
            <p className="text-sm text-green-400 mt-1">Stocks up today</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-red-900/20 to-red-800/10 border-red-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-red-400">Losers</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {screenerStocks.filter(s => s.changePercent < 0).length}
            </div>
            <p className="text-sm text-red-400 mt-1">Stocks down today</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Market Breadth</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {((screenerStocks.filter(s => s.changePercent > 0).length / screenerStocks.length) * 100).toFixed(1)}%
            </div>
            <p className="text-sm text-slate-400 mt-1">Advancing stocks</p>
          </CardContent>
        </Card>
      </div>

      {/* Sector Performance */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Sector Performance</CardTitle>
          <p className="text-sm text-slate-400">Performance by sector (YTD %)</p>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={sectorPerformance}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="sector" stroke="#64748b" />
              <YAxis stroke="#64748b" />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1e293b",
                  border: "1px solid #334155",
                  borderRadius: "8px",
                  color: "#fff",
                }}
              />
              <Bar dataKey="performance" radius={[8, 8, 0, 0]}>
                {sectorPerformance.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Stock Heatmap Grid */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Stock Performance Heatmap</CardTitle>
          <p className="text-sm text-slate-400">Size represents market cap, color represents daily change</p>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {stocksWithSize.map((stock) => {
              const getSize = () => {
                if (stock.size >= 1000) return "h-40";
                if (stock.size >= 500) return "h-32";
                if (stock.size >= 100) return "h-28";
                return "h-24";
              };

              const getBgColor = () => {
                if (stock.changePercent > 2) return "bg-green-600";
                if (stock.changePercent > 0) return "bg-green-700";
                if (stock.changePercent > -2) return "bg-red-700";
                return "bg-red-600";
              };

              return (
                <div
                  key={stock.symbol}
                  className={`${getSize()} ${getBgColor()} rounded-lg p-4 flex flex-col justify-between hover:opacity-90 transition-opacity cursor-pointer`}
                >
                  <div>
                    <div className="font-mono font-bold text-white text-lg">{stock.symbol}</div>
                    <div className="text-xs text-white/80 mt-1">{stock.sector}</div>
                  </div>
                  <div>
                    <div className="text-white font-semibold">${stock.price.toFixed(2)}</div>
                    <div className="flex items-center gap-1 text-white/90">
                      {stock.changePercent > 0 ? (
                        <TrendingUp className="w-4 h-4" />
                      ) : (
                        <TrendingDown className="w-4 h-4" />
                      )}
                      <span className="text-sm font-medium">
                        {stock.changePercent > 0 ? "+" : ""}{stock.changePercent.toFixed(2)}%
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Top Movers */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Top Gainers */}
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-green-400" />
              <CardTitle className="text-white">Top Gainers</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {[...screenerStocks]
                .sort((a, b) => b.changePercent - a.changePercent)
                .slice(0, 5)
                .map((stock) => (
                  <div
                    key={stock.symbol}
                    className="flex items-center justify-between p-3 bg-slate-800/30 rounded-lg"
                  >
                    <div>
                      <div className="text-white font-mono font-semibold">{stock.symbol}</div>
                      <div className="text-sm text-slate-400">{stock.name}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-white">${stock.price.toFixed(2)}</div>
                      <div className="text-green-400 font-semibold">
                        +{stock.changePercent.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>

        {/* Top Losers */}
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <div className="flex items-center gap-2">
              <TrendingDown className="w-5 h-5 text-red-400" />
              <CardTitle className="text-white">Top Losers</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {[...screenerStocks]
                .sort((a, b) => a.changePercent - b.changePercent)
                .slice(0, 5)
                .map((stock) => (
                  <div
                    key={stock.symbol}
                    className="flex items-center justify-between p-3 bg-slate-800/30 rounded-lg"
                  >
                    <div>
                      <div className="text-white font-mono font-semibold">{stock.symbol}</div>
                      <div className="text-sm text-slate-400">{stock.name}</div>
                    </div>
                    <div className="text-right">
                      <div className="text-white">${stock.price.toFixed(2)}</div>
                      <div className="text-red-400 font-semibold">
                        {stock.changePercent.toFixed(2)}%
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Sector Breakdown */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Sector Breakdown</CardTitle>
          <p className="text-sm text-slate-400">Stocks by sector</p>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {Array.from(new Set(screenerStocks.map(s => s.sector))).map((sector) => {
              const sectorStocks = screenerStocks.filter(s => s.sector === sector);
              const avgChange = sectorStocks.reduce((sum, s) => sum + s.changePercent, 0) / sectorStocks.length;
              
              return (
                <div
                  key={sector}
                  className="p-4 bg-slate-800/30 rounded-lg border border-slate-700"
                >
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="text-white font-semibold">{sector}</h4>
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: getSectorColor(sector) }}
                    />
                  </div>
                  <div className="text-2xl text-white mb-1">
                    {avgChange > 0 ? "+" : ""}{avgChange.toFixed(2)}%
                  </div>
                  <div className="text-sm text-slate-400">
                    {sectorStocks.length} stocks
                  </div>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
