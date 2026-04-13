import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { BookOpen, TrendingUp, TrendingDown, Star, Target } from "lucide-react";
import { tradeJournal } from "../data/optionsData";
import { Progress } from "../components/ui/progress";

export function TradeJournal() {
  const getRatingStars = (rating: number) => {
    return Array.from({ length: 5 }, (_, i) => (
      <Star
        key={i}
        className={`w-4 h-4 ${
          i < rating ? "text-yellow-400 fill-yellow-400" : "text-slate-600"
        }`}
      />
    ));
  };

  const completedTrades = tradeJournal.filter((t) => t.exitDate);
  const totalProfit = completedTrades.reduce((sum, t) => sum + (t.profitLoss || 0), 0);
  const avgProfit = totalProfit / completedTrades.length;
  const winningTrades = completedTrades.filter((t) => (t.profitLoss || 0) > 0).length;
  const winRate = (winningTrades / completedTrades.length) * 100;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Trade Journal</h2>
        <p className="text-slate-400 mt-1">Document and learn from every trade</p>
      </div>

      {/* Performance Summary */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Trades</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{tradeJournal.length}</div>
            <p className="text-sm text-slate-400 mt-1">{completedTrades.length} completed</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Total Profit</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {totalProfit >= 0 ? "+" : ""}${totalProfit.toFixed(2)}
            </div>
            <p className="text-sm text-green-400 mt-1">From closed trades</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Win Rate</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{winRate.toFixed(1)}%</div>
            <p className="text-sm text-slate-400 mt-1">
              {winningTrades}W / {completedTrades.length - winningTrades}L
            </p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Avg Profit</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {avgProfit >= 0 ? "+" : ""}${avgProfit.toFixed(2)}
            </div>
            <p className="text-sm text-slate-400 mt-1">Per closed trade</p>
          </CardContent>
        </Card>
      </div>

      {/* Trade Entries */}
      <div className="space-y-4">
        {tradeJournal.map((trade) => {
          const isWinning = (trade.profitLoss || 0) > 0;
          const isCompleted = !!trade.exitDate;

          return (
            <Card key={trade.id} className="bg-slate-900 border-slate-800">
              <CardHeader>
                <div className="flex items-start justify-between">
                  <div className="flex items-center gap-3">
                    <div
                      className={`p-3 rounded-lg ${
                        isCompleted
                          ? isWinning
                            ? "bg-green-600/20"
                            : "bg-red-600/20"
                          : "bg-blue-600/20"
                      }`}
                    >
                      {isCompleted ? (
                        isWinning ? (
                          <TrendingUp className="w-6 h-6 text-green-400" />
                        ) : (
                          <TrendingDown className="w-6 h-6 text-red-400" />
                        )
                      ) : (
                        <Target className="w-6 h-6 text-blue-400" />
                      )}
                    </div>
                    <div>
                      <h3 className="text-white font-mono font-semibold text-xl">{trade.symbol}</h3>
                      <div className="flex items-center gap-2 mt-1">
                        <Badge className="bg-slate-700 text-slate-300 text-xs">{trade.strategy}</Badge>
                        {trade.tags.map((tag) => (
                          <Badge key={tag} variant="outline" className="border-slate-700 text-slate-400 text-xs">
                            {tag}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div className="text-right">
                    {isCompleted && (
                      <>
                        <div
                          className={`text-2xl font-semibold ${
                            isWinning ? "text-green-400" : "text-red-400"
                          }`}
                        >
                          {trade.profitLoss! >= 0 ? "+" : ""}${trade.profitLoss!.toFixed(2)}
                        </div>
                        <div
                          className={`text-sm ${isWinning ? "text-green-400" : "text-red-400"}`}
                        >
                          {trade.profitLossPercent! >= 0 ? "+" : ""}
                          {trade.profitLossPercent!.toFixed(2)}%
                        </div>
                      </>
                    )}
                    {!isCompleted && (
                      <Badge className="bg-blue-600/20 text-blue-400">Open Position</Badge>
                    )}
                  </div>
                </div>
              </CardHeader>

              <CardContent>
                {/* Trade Details */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4 bg-slate-800/30 rounded-lg mb-4">
                  <div>
                    <div className="text-xs text-slate-400">Entry Date</div>
                    <div className="text-white font-medium">
                      {new Date(trade.entryDate).toLocaleDateString()}
                    </div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Entry Price</div>
                    <div className="text-white font-medium">${trade.entryPrice.toFixed(2)}</div>
                  </div>
                  {isCompleted && (
                    <>
                      <div>
                        <div className="text-xs text-slate-400">Exit Date</div>
                        <div className="text-white font-medium">
                          {new Date(trade.exitDate!).toLocaleDateString()}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Exit Price</div>
                        <div className="text-white font-medium">${trade.exitPrice!.toFixed(2)}</div>
                      </div>
                    </>
                  )}
                  <div>
                    <div className="text-xs text-slate-400">Position Size</div>
                    <div className="text-white font-medium">{trade.shares} shares</div>
                  </div>
                  <div>
                    <div className="text-xs text-slate-400">Total Value</div>
                    <div className="text-white font-medium">
                      ${(trade.shares * trade.entryPrice).toLocaleString()}
                    </div>
                  </div>
                </div>

                {/* Trade Notes */}
                <div className="space-y-3">
                  <div className="p-4 bg-slate-800/30 rounded-lg">
                    <div className="flex items-center gap-2 mb-2">
                      <BookOpen className="w-4 h-4 text-blue-400" />
                      <span className="text-sm text-slate-400 font-medium">Trade Notes</span>
                    </div>
                    <p className="text-slate-300">{trade.notes}</p>
                  </div>

                  {/* Emotions */}
                  {trade.emotions.length > 0 && (
                    <div className="p-4 bg-slate-800/30 rounded-lg">
                      <div className="text-sm text-slate-400 font-medium mb-2">Emotional State</div>
                      <div className="flex gap-2">
                        {trade.emotions.map((emotion) => (
                          <Badge key={emotion} className="bg-purple-600/20 text-purple-400">
                            {emotion}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Mistakes */}
                  {trade.mistakes && (
                    <div className="p-4 bg-red-950/20 rounded-lg border border-red-800/30">
                      <div className="text-sm text-red-400 font-medium mb-2">⚠️ Mistakes</div>
                      <p className="text-slate-300">{trade.mistakes}</p>
                    </div>
                  )}

                  {/* Lessons */}
                  {trade.lessons && (
                    <div className="p-4 bg-green-950/20 rounded-lg border border-green-800/30">
                      <div className="text-sm text-green-400 font-medium mb-2">✓ Lessons Learned</div>
                      <p className="text-slate-300">{trade.lessons}</p>
                    </div>
                  )}

                  {/* Rating */}
                  <div className="flex items-center justify-between p-4 bg-slate-800/30 rounded-lg">
                    <span className="text-sm text-slate-400">Trade Execution Rating</span>
                    <div className="flex gap-1">{getRatingStars(trade.rating)}</div>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      {/* Insights */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">Trading Insights</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Best Strategy</h4>
              <p className="text-2xl text-green-400 mb-1">Momentum AI</p>
              <Progress value={85} className="h-2" />
              <p className="text-xs text-slate-400 mt-2">85% win rate</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Most Profitable</h4>
              <p className="text-2xl text-white mb-1">NVDA</p>
              <p className="text-sm text-green-400">+$8,239 total</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <h4 className="text-white font-semibold mb-2">Common Mistake</h4>
              <p className="text-sm text-slate-300">Entering without trend confirmation</p>
              <p className="text-xs text-slate-500 mt-1">Identified in 1 losing trade</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
