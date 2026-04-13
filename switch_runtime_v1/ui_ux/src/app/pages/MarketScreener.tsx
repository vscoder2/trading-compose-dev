import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Button } from "../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Badge } from "../components/ui/badge";
import { Search, Filter, TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { screenerStocks, type ScreenerCriteria } from "../data/advancedData";

export function MarketScreener() {
  const [criteria, setCriteria] = useState<ScreenerCriteria>({});
  const [filteredStocks, setFilteredStocks] = useState(screenerStocks);

  const applyFilters = () => {
    let filtered = screenerStocks;

    if (criteria.priceMin) {
      filtered = filtered.filter(s => s.price >= criteria.priceMin!);
    }
    if (criteria.priceMax) {
      filtered = filtered.filter(s => s.price <= criteria.priceMax!);
    }
    if (criteria.volumeMin) {
      filtered = filtered.filter(s => s.volume >= criteria.volumeMin!);
    }
    if (criteria.changePercentMin) {
      filtered = filtered.filter(s => s.changePercent >= criteria.changePercentMin!);
    }
    if (criteria.rsiMin) {
      filtered = filtered.filter(s => s.rsi >= criteria.rsiMin!);
    }
    if (criteria.rsiMax) {
      filtered = filtered.filter(s => s.rsi <= criteria.rsiMax!);
    }
    if (criteria.sector && criteria.sector !== "all") {
      filtered = filtered.filter(s => s.sector === criteria.sector);
    }

    setFilteredStocks(filtered);
  };

  const resetFilters = () => {
    setCriteria({});
    setFilteredStocks(screenerStocks);
  };

  const presetFilters = [
    {
      name: "Momentum Leaders",
      filter: () => {
        setCriteria({ rsiMin: 60, changePercentMin: 2 });
        setFilteredStocks(screenerStocks.filter(s => s.rsi >= 60 && s.changePercent >= 2));
      }
    },
    {
      name: "Oversold Bargains",
      filter: () => {
        setCriteria({ rsiMax: 30 });
        setFilteredStocks(screenerStocks.filter(s => s.rsi <= 30));
      }
    },
    {
      name: "High Volume Movers",
      filter: () => {
        setCriteria({ volumeMin: 30000000, changePercentMin: 1 });
        setFilteredStocks(screenerStocks.filter(s => s.volume >= 30000000 && Math.abs(s.changePercent) >= 1));
      }
    },
    {
      name: "Dividend Stocks",
      filter: () => {
        setCriteria({ dividendYieldMin: 2 });
        setFilteredStocks(screenerStocks.filter(s => s.dividendYield >= 2));
      }
    },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Market Screener</h2>
        <p className="text-slate-400 mt-1">Find stocks matching your criteria</p>
      </div>

      {/* Preset Filters */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Quick Filters</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {presetFilters.map((preset) => (
              <Button
                key={preset.name}
                variant="outline"
                onClick={preset.filter}
                className="border-slate-700 text-slate-300 hover:bg-slate-800"
              >
                {preset.name}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Custom Filters */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Custom Filters</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            <div>
              <Label className="text-slate-300">Min Price ($)</Label>
              <Input
                type="number"
                placeholder="0"
                value={criteria.priceMin || ""}
                onChange={(e) => setCriteria({ ...criteria, priceMin: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Max Price ($)</Label>
              <Input
                type="number"
                placeholder="10000"
                value={criteria.priceMax || ""}
                onChange={(e) => setCriteria({ ...criteria, priceMax: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Min Volume</Label>
              <Input
                type="number"
                placeholder="0"
                value={criteria.volumeMin || ""}
                onChange={(e) => setCriteria({ ...criteria, volumeMin: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Sector</Label>
              <Select
                value={criteria.sector || "all"}
                onValueChange={(value) => setCriteria({ ...criteria, sector: value })}
              >
                <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-slate-800 border-slate-700">
                  <SelectItem value="all" className="text-white">All Sectors</SelectItem>
                  <SelectItem value="Technology" className="text-white">Technology</SelectItem>
                  <SelectItem value="Financial" className="text-white">Financial</SelectItem>
                  <SelectItem value="Healthcare" className="text-white">Healthcare</SelectItem>
                  <SelectItem value="Energy" className="text-white">Energy</SelectItem>
                  <SelectItem value="Consumer" className="text-white">Consumer</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label className="text-slate-300">Min Change %</Label>
              <Input
                type="number"
                placeholder="-100"
                value={criteria.changePercentMin || ""}
                onChange={(e) => setCriteria({ ...criteria, changePercentMin: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Min RSI</Label>
              <Input
                type="number"
                placeholder="0"
                value={criteria.rsiMin || ""}
                onChange={(e) => setCriteria({ ...criteria, rsiMin: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Max RSI</Label>
              <Input
                type="number"
                placeholder="100"
                value={criteria.rsiMax || ""}
                onChange={(e) => setCriteria({ ...criteria, rsiMax: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
            <div>
              <Label className="text-slate-300">Min Dividend Yield %</Label>
              <Input
                type="number"
                placeholder="0"
                value={criteria.dividendYieldMin || ""}
                onChange={(e) => setCriteria({ ...criteria, dividendYieldMin: parseFloat(e.target.value) || undefined })}
                className="bg-slate-800 border-slate-700 text-white mt-1"
              />
            </div>
          </div>

          <div className="flex gap-3 mt-6">
            <Button onClick={applyFilters} className="bg-blue-600 hover:bg-blue-700">
              <Search className="w-4 h-4 mr-2" />
              Apply Filters
            </Button>
            <Button onClick={resetFilters} variant="outline" className="border-slate-700 text-slate-300 hover:bg-slate-800">
              Reset
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Results */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">
            Results ({filteredStocks.length} stocks)
          </CardTitle>
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
                  <th className="text-right py-3 px-4 text-sm text-slate-400">RSI</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">MACD</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">P/E</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Div Yield</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Sector</th>
                </tr>
              </thead>
              <tbody>
                {filteredStocks.map((stock) => (
                  <tr key={stock.symbol} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="py-3 px-4">
                      <span className="text-white font-mono font-semibold">{stock.symbol}</span>
                    </td>
                    <td className="py-3 px-4 text-slate-300">{stock.name}</td>
                    <td className="py-3 px-4 text-right text-white">${stock.price.toFixed(2)}</td>
                    <td className="py-3 px-4 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {stock.change >= 0 ? (
                          <>
                            <ArrowUpRight className="w-4 h-4 text-green-500" />
                            <span className="text-green-500">+{stock.changePercent.toFixed(2)}%</span>
                          </>
                        ) : (
                          <>
                            <ArrowDownRight className="w-4 h-4 text-red-500" />
                            <span className="text-red-500">{stock.changePercent.toFixed(2)}%</span>
                          </>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4 text-right text-slate-400">
                      {(stock.volume / 1000000).toFixed(1)}M
                    </td>
                    <td className="py-3 px-4 text-right">
                      <Badge className={
                        stock.rsi > 70 ? "bg-red-600/20 text-red-400" :
                        stock.rsi < 30 ? "bg-green-600/20 text-green-400" :
                        "bg-slate-600/20 text-slate-400"
                      }>
                        {stock.rsi.toFixed(1)}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-right">
                      <Badge className={
                        stock.macd === "bullish" ? "bg-green-600/20 text-green-400" :
                        stock.macd === "bearish" ? "bg-red-600/20 text-red-400" :
                        "bg-slate-600/20 text-slate-400"
                      }>
                        {stock.macd}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-right text-slate-300">{stock.peRatio.toFixed(1)}</td>
                    <td className="py-3 px-4 text-right text-slate-300">{stock.dividendYield.toFixed(2)}%</td>
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="border-slate-700 text-slate-300">
                        {stock.sector}
                      </Badge>
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
