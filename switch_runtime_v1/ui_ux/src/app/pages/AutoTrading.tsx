import { Bot, Pause, Settings, Play } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Switch } from "../components/ui/switch";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { mockBots } from "../data/mockData";
import { useState } from "react";

export function AutoTrading() {
  const [bots, setBots] = useState(mockBots);

  const toggleBot = (id: string) => {
    setBots(
      bots.map((bot) =>
        bot.id === id
          ? { ...bot, status: bot.status === "active" ? "paused" : "active" }
          : bot,
      ),
    );
  };

  const activeBots = bots.filter((bot) => bot.status === "active").length;
  const totalTrades = bots.reduce((sum, bot) => sum + bot.totalTrades, 0);
  const totalProfit = bots.reduce((sum, bot) => sum + bot.profit, 0);
  const avgWinRate = bots.length ? bots.reduce((sum, bot) => sum + bot.winRate, 0) / bots.length : 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Active Bots</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{activeBots}/{bots.length}</div>
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
            <CardTitle className="text-sm text-slate-400">Avg Win Rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-3xl text-white">{avgWinRate.toFixed(1)}%</div>
          </CardContent>
        </Card>
      </div>

      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-white">Runtime Bot Control</CardTitle>
              <p className="text-sm text-slate-400 mt-1">Displays runtime snapshot profiles and telemetry.</p>
            </div>
            <Button className="bg-blue-600 hover:bg-blue-700">
              <Bot className="w-4 h-4 mr-2" />
              Runtime Mode
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {bots.map((bot) => (
              <div key={bot.id} className="p-6 bg-slate-800/50 rounded-lg border border-slate-700">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-3">
                      <div className="p-2 bg-blue-600/20 rounded-lg">
                        <Bot className="w-5 h-5 text-blue-400" />
                      </div>
                      <div>
                        <h3 className="text-lg text-white font-semibold">{bot.name}</h3>
                        <p className="text-sm text-slate-400 mt-1">{bot.strategy}</p>
                      </div>
                    </div>

                    <div className="grid grid-cols-4 gap-4 mt-4">
                      <div>
                        <p className="text-xs text-slate-500 uppercase">Status</p>
                        <Badge
                          className={`mt-1 ${
                            bot.status === "active"
                              ? "bg-green-600/20 text-green-400 hover:bg-green-600/30"
                              : bot.status === "paused"
                                ? "bg-yellow-600/20 text-yellow-400 hover:bg-yellow-600/30"
                                : "bg-slate-600/20 text-slate-400 hover:bg-slate-600/30"
                          }`}
                        >
                          {bot.status}
                        </Badge>
                      </div>
                      <div>
                        <p className="text-xs text-slate-500 uppercase">Total Trades</p>
                        <p className="text-white mt-1">{bot.totalTrades}</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-500 uppercase">Win Rate</p>
                        <p className="text-white mt-1">{bot.winRate.toFixed(1)}%</p>
                      </div>
                      <div>
                        <p className="text-xs text-slate-500 uppercase">Profit</p>
                        <p className={bot.profit >= 0 ? "text-green-400 mt-1" : "text-red-400 mt-1"}>
                          {bot.profit >= 0 ? "+" : ""}${bot.profit.toLocaleString()}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-3">
                    <Button
                      variant="outline"
                      size="sm"
                      className="border-slate-700 text-slate-300 hover:bg-slate-800"
                    >
                      <Settings className="w-4 h-4 mr-2" />
                      Configure
                    </Button>
                    <div className="flex items-center gap-2">
                      {bot.status === "active" ? (
                        <Pause className="w-4 h-4 text-slate-400" />
                      ) : (
                        <Play className="w-4 h-4 text-slate-400" />
                      )}
                      <Switch
                        checked={bot.status === "active"}
                        onCheckedChange={() => toggleBot(bot.id)}
                      />
                    </div>
                  </div>
                </div>
              </div>
            ))}
            {bots.length === 0 && (
              <div className="text-center text-slate-500 py-8">No runtime bot payload available.</div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

