import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { Search, Plus, Star, TrendingUp, TrendingDown, X } from "lucide-react";
import { mockStocks } from "../data/mockData";

export function Watchlist() {
  const [watchlist, setWatchlist] = useState(mockStocks.slice(0, 4));
  const [searchQuery, setSearchQuery] = useState("");
  const [showSearch, setShowSearch] = useState(false);

  const filteredStocks = mockStocks.filter(
    stock =>
      stock.symbol.toLowerCase().includes(searchQuery.toLowerCase()) ||
      stock.name.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const addToWatchlist = (stock: typeof mockStocks[0]) => {
    if (!watchlist.find(s => s.symbol === stock.symbol)) {
      setWatchlist([...watchlist, stock]);
      setSearchQuery("");
      setShowSearch(false);
    }
  };

  const removeFromWatchlist = (symbol: string) => {
    setWatchlist(watchlist.filter(s => s.symbol !== symbol));
  };

  return (
    <Card className="bg-slate-900 border-slate-800">
      <CardHeader>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Star className="w-5 h-5 text-yellow-400 fill-yellow-400" />
            <CardTitle className="text-white">Watchlist</CardTitle>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="border-slate-700 text-slate-300 hover:bg-slate-800"
            onClick={() => setShowSearch(!showSearch)}
          >
            <Plus className="w-4 h-4 mr-2" />
            Add Stock
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {showSearch && (
          <div className="mb-4">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <Input
                placeholder="Search stocks..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10 bg-slate-800 border-slate-700 text-white"
              />
            </div>
            {searchQuery && (
              <div className="mt-2 max-h-48 overflow-y-auto bg-slate-800 rounded-lg border border-slate-700">
                {filteredStocks.map((stock) => (
                  <button
                    key={stock.symbol}
                    onClick={() => addToWatchlist(stock)}
                    className="w-full px-4 py-2 flex items-center justify-between hover:bg-slate-700 transition-colors text-left"
                  >
                    <div>
                      <div className="text-white font-mono">{stock.symbol}</div>
                      <div className="text-sm text-slate-400">{stock.name}</div>
                    </div>
                    <div className="text-white">${stock.price.toFixed(2)}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        <div className="space-y-2">
          {watchlist.map((stock) => (
            <div
              key={stock.symbol}
              className="p-3 bg-slate-800/50 rounded-lg flex items-center justify-between hover:bg-slate-800 transition-colors group"
            >
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-white font-mono font-semibold">{stock.symbol}</span>
                  <button
                    onClick={() => removeFromWatchlist(stock.symbol)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity"
                  >
                    <X className="w-4 h-4 text-slate-500 hover:text-red-400" />
                  </button>
                </div>
                <div className="text-sm text-slate-400">{stock.name}</div>
              </div>
              <div className="text-right">
                <div className="text-white font-semibold">${stock.price.toFixed(2)}</div>
                <div className="flex items-center justify-end gap-1">
                  {stock.change >= 0 ? (
                    <>
                      <TrendingUp className="w-3 h-3 text-green-500" />
                      <span className="text-sm text-green-500">
                        +{stock.changePercent}%
                      </span>
                    </>
                  ) : (
                    <>
                      <TrendingDown className="w-3 h-3 text-red-500" />
                      <span className="text-sm text-red-500">
                        {stock.changePercent}%
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
