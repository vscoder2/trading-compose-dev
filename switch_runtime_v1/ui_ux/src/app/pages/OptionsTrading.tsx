import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Search, TrendingUp, TrendingDown, Info } from "lucide-react";
import { optionChains, optionsStrategies, optionsFlow } from "../data/optionsData";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../components/ui/tooltip";

export function OptionsTrading() {
  const [selectedSymbol, setSelectedSymbol] = useState("AAPL");
  const [selectedExpiration, setSelectedExpiration] = useState("2026-05-15");

  const filteredChains = optionChains.filter(
    (chain) => chain.symbol === selectedSymbol && chain.expirationDate === selectedExpiration
  );

  const getMoneyness = (strike: number, spotPrice: number) => {
    const diff = ((spotPrice - strike) / strike) * 100;
    if (Math.abs(diff) < 2) return "ATM";
    if (spotPrice > strike) return "ITM";
    return "OTM";
  };

  const spotPrice = 178.45; // AAPL current price

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Options Trading</h2>
        <p className="text-slate-400 mt-1">Trade options with advanced Greeks and analytics</p>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Options Volume</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">2.4M</div>
            <p className="text-sm text-green-400 mt-1">+12.3% vs avg</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Put/Call Ratio</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">0.84</div>
            <p className="text-sm text-slate-400 mt-1">Slightly bullish</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Implied Volatility</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">27.8%</div>
            <p className="text-sm text-yellow-400 mt-1">Moderate</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Max Pain</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">$180</div>
            <p className="text-sm text-slate-400 mt-1">Strike level</p>
          </CardContent>
        </Card>
      </div>

      <Tabs defaultValue="chain" className="w-full">
        <TabsList className="grid w-full md:w-auto grid-cols-3 bg-slate-800">
          <TabsTrigger value="chain">Options Chain</TabsTrigger>
          <TabsTrigger value="strategies">Strategies</TabsTrigger>
          <TabsTrigger value="flow">Unusual Flow</TabsTrigger>
        </TabsList>

        {/* Options Chain */}
        <TabsContent value="chain" className="mt-6 space-y-4">
          {/* Symbol Search */}
          <Card className="bg-slate-900 border-slate-800">
            <CardContent className="pt-6">
              <div className="flex items-center gap-4">
                <div className="flex-1 relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                  <Input
                    placeholder="Search symbol..."
                    value={selectedSymbol}
                    onChange={(e) => setSelectedSymbol(e.target.value.toUpperCase())}
                    className="pl-10 bg-slate-800 border-slate-700 text-white"
                  />
                </div>
                <div className="text-white">
                  <div className="text-sm text-slate-400">Current Price</div>
                  <div className="text-xl font-semibold">${spotPrice}</div>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Options Chain Table */}
          <Card className="bg-slate-900 border-slate-800">
            <CardHeader>
              <CardTitle className="text-white">
                {selectedSymbol} Options - Expiry: {new Date(selectedExpiration).toLocaleDateString()}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th colSpan={6} className="text-center py-3 px-4 text-sm text-green-400 bg-green-950/20">
                        CALLS
                      </th>
                      <th className="py-3 px-4 text-sm text-slate-400 bg-slate-800">Strike</th>
                      <th colSpan={6} className="text-center py-3 px-4 text-sm text-red-400 bg-red-950/20">
                        PUTS
                      </th>
                    </tr>
                    <tr className="border-b border-slate-800 text-xs">
                      <th className="text-left py-2 px-2 text-slate-400">Bid</th>
                      <th className="text-left py-2 px-2 text-slate-400">Ask</th>
                      <th className="text-right py-2 px-2 text-slate-400">Vol</th>
                      <th className="text-right py-2 px-2 text-slate-400">OI</th>
                      <th className="text-right py-2 px-2 text-slate-400">IV</th>
                      <th className="text-right py-2 px-2 text-slate-400">Delta</th>
                      <th className="text-center py-2 px-4 text-white bg-slate-800 font-semibold">Price</th>
                      <th className="text-left py-2 px-2 text-slate-400">Delta</th>
                      <th className="text-left py-2 px-2 text-slate-400">IV</th>
                      <th className="text-right py-2 px-2 text-slate-400">OI</th>
                      <th className="text-right py-2 px-2 text-slate-400">Vol</th>
                      <th className="text-right py-2 px-2 text-slate-400">Ask</th>
                      <th className="text-right py-2 px-2 text-slate-400">Bid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredChains.map((chain) => {
                      const moneyness = getMoneyness(chain.strike, spotPrice);
                      const isAtm = moneyness === "ATM";
                      
                      return (
                        <tr
                          key={chain.strike}
                          className={`border-b border-slate-800/50 hover:bg-slate-800/30 ${
                            isAtm ? "bg-blue-950/20" : ""
                          }`}
                        >
                          {/* Calls */}
                          <td className="py-3 px-2 text-green-400">${chain.callBid.toFixed(2)}</td>
                          <td className="py-3 px-2 text-green-400">${chain.callAsk.toFixed(2)}</td>
                          <td className="py-3 px-2 text-right text-slate-300">
                            {chain.callVolume.toLocaleString()}
                          </td>
                          <td className="py-3 px-2 text-right text-slate-300">
                            {chain.callOpenInterest.toLocaleString()}
                          </td>
                          <td className="py-3 px-2 text-right text-slate-300">{chain.callIV}%</td>
                          <td className="py-3 px-2 text-right text-green-400">{chain.callDelta.toFixed(2)}</td>

                          {/* Strike */}
                          <td className="py-3 px-4 text-center bg-slate-800">
                            <div className="flex items-center justify-center gap-2">
                              <span className="text-white font-semibold">${chain.strike}</span>
                              {isAtm && (
                                <Badge className="bg-blue-600/20 text-blue-400 text-xs">ATM</Badge>
                              )}
                            </div>
                          </td>

                          {/* Puts */}
                          <td className="py-3 px-2 text-red-400">{chain.putDelta.toFixed(2)}</td>
                          <td className="py-3 px-2 text-slate-300">{chain.putIV}%</td>
                          <td className="py-3 px-2 text-right text-slate-300">
                            {chain.putOpenInterest.toLocaleString()}
                          </td>
                          <td className="py-3 px-2 text-right text-slate-300">
                            {chain.putVolume.toLocaleString()}
                          </td>
                          <td className="py-3 px-2 text-right text-red-400">${chain.putAsk.toFixed(2)}</td>
                          <td className="py-3 px-2 text-right text-red-400">${chain.putBid.toFixed(2)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {/* Greeks Explanation */}
              <div className="mt-6 p-4 bg-slate-800/50 rounded-lg">
                <h4 className="text-white font-semibold mb-3">Greeks Reference</h4>
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 text-sm">
                  <div>
                    <span className="text-blue-400 font-medium">Delta:</span>
                    <p className="text-slate-400">Rate of change per $1 move in stock</p>
                  </div>
                  <div>
                    <span className="text-blue-400 font-medium">Gamma:</span>
                    <p className="text-slate-400">Rate of change of delta</p>
                  </div>
                  <div>
                    <span className="text-blue-400 font-medium">Theta:</span>
                    <p className="text-slate-400">Time decay per day</p>
                  </div>
                  <div>
                    <span className="text-blue-400 font-medium">Vega:</span>
                    <p className="text-slate-400">Sensitivity to volatility changes</p>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* Options Strategies */}
        <TabsContent value="strategies" className="mt-6 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {optionsStrategies.map((strategy) => (
              <Card key={strategy.id} className="bg-slate-900 border-slate-800">
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div>
                      <CardTitle className="text-white">{strategy.name}</CardTitle>
                      <p className="text-sm text-slate-400 mt-1">{strategy.description}</p>
                    </div>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger>
                          <Info className="w-5 h-5 text-slate-400" />
                        </TooltipTrigger>
                        <TooltipContent className="bg-slate-800 border-slate-700">
                          <p className="text-white">Click to build this strategy</p>
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-4">
                    <div className="flex items-center gap-3">
                      <Badge
                        className={
                          strategy.type === "bullish"
                            ? "bg-green-600/20 text-green-400"
                            : strategy.type === "bearish"
                            ? "bg-red-600/20 text-red-400"
                            : "bg-blue-600/20 text-blue-400"
                        }
                      >
                        {strategy.type}
                      </Badge>
                      <Badge
                        className={
                          strategy.riskLevel === "high"
                            ? "bg-red-600/20 text-red-400"
                            : strategy.riskLevel === "medium"
                            ? "bg-yellow-600/20 text-yellow-400"
                            : "bg-green-600/20 text-green-400"
                        }
                      >
                        {strategy.riskLevel} risk
                      </Badge>
                    </div>

                    <div className="grid grid-cols-2 gap-3 p-3 bg-slate-800/50 rounded-lg">
                      <div>
                        <div className="text-xs text-slate-400">Max Profit</div>
                        <div className="text-sm text-green-400 font-medium">{strategy.maxProfit}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Max Loss</div>
                        <div className="text-sm text-red-400 font-medium">{strategy.maxLoss}</div>
                      </div>
                    </div>

                    <div>
                      <div className="text-sm text-slate-400 mb-2">Strategy Legs:</div>
                      <div className="space-y-2">
                        {strategy.legs.map((leg, idx) => (
                          <div key={idx} className="flex items-center gap-2 text-sm">
                            {leg.action === "buy" ? (
                              <TrendingUp className="w-4 h-4 text-green-400" />
                            ) : (
                              <TrendingDown className="w-4 h-4 text-red-400" />
                            )}
                            <span className="text-white">
                              {leg.action.toUpperCase()} {leg.quantity}x ${leg.strike} {leg.type.toUpperCase()}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>

                    <Button className="w-full bg-blue-600 hover:bg-blue-700">
                      Build Strategy
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </TabsContent>

        {/* Unusual Options Flow */}
        <TabsContent value="flow" className="mt-6 space-y-4">
          <Card className="bg-gradient-to-br from-purple-900/20 to-purple-800/10 border-purple-800/50">
            <CardHeader>
              <CardTitle className="text-white">Unusual Options Activity</CardTitle>
              <p className="text-sm text-slate-400">
                Large institutional trades with unusual premium and size
              </p>
            </CardHeader>
            <CardContent>
              <div className="space-y-4">
                {optionsFlow.map((flow) => (
                  <div
                    key={flow.id}
                    className="p-5 bg-slate-900/50 rounded-lg border border-slate-700/50"
                  >
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-3">
                        <div
                          className={`p-3 rounded-lg ${
                            flow.sentiment === "bullish"
                              ? "bg-green-600/20"
                              : flow.sentiment === "bearish"
                              ? "bg-red-600/20"
                              : "bg-slate-600/20"
                          }`}
                        >
                          {flow.type === "call" ? (
                            <TrendingUp
                              className={`w-6 h-6 ${
                                flow.sentiment === "bullish" ? "text-green-400" : "text-slate-400"
                              }`}
                            />
                          ) : (
                            <TrendingDown
                              className={`w-6 h-6 ${
                                flow.sentiment === "bearish" ? "text-red-400" : "text-slate-400"
                              }`}
                            />
                          )}
                        </div>
                        <div>
                          <h4 className="text-white font-semibold text-lg">{flow.symbol}</h4>
                          <p className="text-sm text-slate-400">
                            {new Date(flow.timestamp).toLocaleString()}
                          </p>
                        </div>
                      </div>
                      <Badge className="bg-yellow-600/20 text-yellow-400 border-yellow-600/30">
                        Unusual Activity
                      </Badge>
                    </div>

                    <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
                      <div>
                        <div className="text-xs text-slate-400">Type</div>
                        <div className="text-white font-semibold uppercase">{flow.type}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Strike</div>
                        <div className="text-white font-semibold">${flow.strike}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Expiration</div>
                        <div className="text-white font-semibold">
                          {new Date(flow.expiration).toLocaleDateString()}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Premium</div>
                        <div className="text-green-400 font-semibold">
                          ${(flow.premium / 1000).toFixed(0)}K
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Size</div>
                        <div className="text-white font-semibold">{flow.size} contracts</div>
                      </div>
                    </div>

                    <div className="mt-4 pt-4 border-t border-slate-700 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Badge
                          className={
                            flow.sentiment === "bullish"
                              ? "bg-green-600/20 text-green-400"
                              : flow.sentiment === "bearish"
                              ? "bg-red-600/20 text-red-400"
                              : "bg-slate-600/20 text-slate-400"
                          }
                        >
                          {flow.sentiment}
                        </Badge>
                        <Badge
                          className={
                            flow.aggressor === "buy"
                              ? "bg-blue-600/20 text-blue-400"
                              : "bg-orange-600/20 text-orange-400"
                          }
                        >
                          Aggressive {flow.aggressor}
                        </Badge>
                      </div>
                      <Button size="sm" variant="outline" className="border-slate-700 text-slate-300">
                        View Details
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
