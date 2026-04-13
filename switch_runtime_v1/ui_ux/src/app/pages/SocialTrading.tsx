import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Avatar, AvatarFallback } from "../components/ui/avatar";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { Users, TrendingUp, Copy, Eye, CheckCircle, Award } from "lucide-react";
import { topTraders } from "../data/advancedData";
import { toast } from "sonner";

export function SocialTrading() {
  const handleCopyTrader = (traderName: string) => {
    toast.success(`Now copying ${traderName}'s trades`);
  };

  const handleFollowTrader = (traderName: string) => {
    toast.success(`Following ${traderName}`);
  };

  const getRiskColor = (riskScore: number) => {
    if (riskScore >= 7) return "bg-red-600/20 text-red-400";
    if (riskScore >= 5) return "bg-yellow-600/20 text-yellow-400";
    return "bg-green-600/20 text-green-400";
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Social Trading</h2>
        <p className="text-slate-400 mt-1">Follow and copy successful traders</p>
      </div>

      {/* Stats Overview */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Active Traders</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">12,847</div>
            <p className="text-sm text-slate-400 mt-1">On the platform</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Following</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">23</div>
            <p className="text-sm text-slate-400 mt-1">Traders you follow</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Copying</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">3</div>
            <p className="text-sm text-slate-400 mt-1">Auto-copy active</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Copy Trading Returns</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">+24.8%</div>
            <p className="text-sm text-green-400 mt-1">Last 30 days</p>
          </CardContent>
        </Card>
      </div>

      {/* Leaderboard */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Award className="w-5 h-5 text-yellow-400" />
            <CardTitle className="text-white">Top Traders Leaderboard</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Best performing traders this month</p>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="monthly" className="w-full">
            <TabsList className="grid w-full md:w-auto grid-cols-3 bg-slate-800">
              <TabsTrigger value="weekly">Weekly</TabsTrigger>
              <TabsTrigger value="monthly">Monthly</TabsTrigger>
              <TabsTrigger value="alltime">All Time</TabsTrigger>
            </TabsList>

            <TabsContent value="monthly" className="mt-6">
              <div className="space-y-4">
                {topTraders.map((trader) => (
                  <div
                    key={trader.id}
                    className="p-5 bg-slate-800/30 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
                  >
                    <div className="flex items-center gap-4">
                      {/* Rank Badge */}
                      <div className={`w-12 h-12 rounded-full flex items-center justify-center font-bold text-lg ${
                        trader.rank === 1 ? "bg-yellow-600/20 text-yellow-400" :
                        trader.rank === 2 ? "bg-slate-400/20 text-slate-400" :
                        trader.rank === 3 ? "bg-orange-600/20 text-orange-400" :
                        "bg-slate-700 text-slate-400"
                      }`}>
                        #{trader.rank}
                      </div>

                      {/* Avatar & Info */}
                      <Avatar className="w-16 h-16 bg-gradient-to-br from-blue-600 to-purple-600">
                        <AvatarFallback className="text-white font-semibold text-lg">
                          {trader.avatar}
                        </AvatarFallback>
                      </Avatar>

                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <h3 className="text-white font-semibold text-lg">{trader.name}</h3>
                          {trader.verified && (
                            <CheckCircle className="w-5 h-5 text-blue-400" />
                          )}
                        </div>
                        <p className="text-sm text-slate-400">{trader.strategy}</p>
                        <div className="flex items-center gap-4 mt-2">
                          <div className="flex items-center gap-1 text-sm text-slate-400">
                            <Eye className="w-4 h-4" />
                            {trader.followers.toLocaleString()} followers
                          </div>
                          <div className="flex items-center gap-1 text-sm text-slate-400">
                            <Copy className="w-4 h-4" />
                            {trader.copiers.toLocaleString()} copiers
                          </div>
                        </div>
                      </div>

                      {/* Stats */}
                      <div className="grid grid-cols-2 gap-4 min-w-[300px]">
                        <div className="text-center p-3 bg-slate-800/50 rounded-lg">
                          <div className="text-sm text-slate-400">Total Return</div>
                          <div className="text-xl text-green-400 font-semibold">+{trader.totalReturn}%</div>
                        </div>
                        <div className="text-center p-3 bg-slate-800/50 rounded-lg">
                          <div className="text-sm text-slate-400">Monthly Return</div>
                          <div className="text-xl text-green-400 font-semibold">+{trader.monthlyReturn}%</div>
                        </div>
                        <div className="text-center p-3 bg-slate-800/50 rounded-lg">
                          <div className="text-sm text-slate-400">Win Rate</div>
                          <div className="text-lg text-white">{trader.winRate}%</div>
                        </div>
                        <div className="text-center p-3 bg-slate-800/50 rounded-lg">
                          <div className="text-sm text-slate-400">Risk Score</div>
                          <Badge className={getRiskColor(trader.riskScore)}>
                            {trader.riskScore}/10
                          </Badge>
                        </div>
                      </div>

                      {/* Actions */}
                      <div className="flex flex-col gap-2 min-w-[120px]">
                        <Button
                          onClick={() => handleCopyTrader(trader.name)}
                          className="bg-blue-600 hover:bg-blue-700"
                          size="sm"
                        >
                          <Copy className="w-4 h-4 mr-2" />
                          Copy
                        </Button>
                        <Button
                          onClick={() => handleFollowTrader(trader.name)}
                          variant="outline"
                          className="border-slate-700 text-slate-300 hover:bg-slate-800"
                          size="sm"
                        >
                          <Users className="w-4 h-4 mr-2" />
                          Follow
                        </Button>
                      </div>
                    </div>

                    {/* Performance Bar */}
                    <div className="mt-4 pt-4 border-t border-slate-700">
                      <div className="flex items-center justify-between text-sm mb-2">
                        <span className="text-slate-400">Total Trades: {trader.totalTrades}</span>
                        <span className="text-slate-400">Avg Trade: +2.3%</span>
                      </div>
                      <div className="h-2 bg-slate-700 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gradient-to-r from-green-600 to-green-400"
                          style={{ width: `${trader.winRate}%` }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="weekly">
              <div className="text-center py-8 text-slate-400">
                Weekly leaderboard data
              </div>
            </TabsContent>

            <TabsContent value="alltime">
              <div className="text-center py-8 text-slate-400">
                All-time leaderboard data
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* My Copy Trading */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">My Copy Trading Portfolio</CardTitle>
          <p className="text-sm text-slate-400">Traders you're currently copying</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {topTraders.slice(0, 3).map((trader) => (
              <div
                key={trader.id}
                className="p-4 bg-slate-800/50 rounded-lg border border-slate-700 flex items-center justify-between"
              >
                <div className="flex items-center gap-3">
                  <Avatar className="w-12 h-12 bg-gradient-to-br from-blue-600 to-purple-600">
                    <AvatarFallback className="text-white font-semibold">
                      {trader.avatar}
                    </AvatarFallback>
                  </Avatar>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-white font-medium">{trader.name}</span>
                      <Badge className="bg-green-600/20 text-green-400">Active</Badge>
                    </div>
                    <p className="text-sm text-slate-400">Copying since March 2026</p>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-green-400 font-semibold">+$2,345</div>
                  <p className="text-sm text-slate-400">Profit from copy</p>
                </div>
                <Button variant="outline" size="sm" className="border-slate-700 text-slate-300 hover:bg-slate-800">
                  Manage
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* How It Works */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">How Copy Trading Works</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="w-12 h-12 rounded-full bg-blue-600/20 flex items-center justify-center mb-3">
                <Users className="w-6 h-6 text-blue-400" />
              </div>
              <h4 className="text-white font-semibold mb-2">1. Choose a Trader</h4>
              <p className="text-sm text-slate-400">
                Browse our leaderboard and select a trader based on their performance, strategy, and risk level.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="w-12 h-12 rounded-full bg-purple-600/20 flex items-center justify-center mb-3">
                <Copy className="w-6 h-6 text-purple-400" />
              </div>
              <h4 className="text-white font-semibold mb-2">2. Set Your Budget</h4>
              <p className="text-sm text-slate-400">
                Decide how much capital to allocate. Their trades will be copied proportionally to your budget.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="w-12 h-12 rounded-full bg-green-600/20 flex items-center justify-center mb-3">
                <TrendingUp className="w-6 h-6 text-green-400" />
              </div>
              <h4 className="text-white font-semibold mb-2">3. Earn Passively</h4>
              <p className="text-sm text-slate-400">
                All their trades are automatically replicated in your account. You earn as they profit.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
