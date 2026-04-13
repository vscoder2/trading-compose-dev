import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Brain, TrendingUp, AlertTriangle, Lightbulb, Target, ArrowRight } from "lucide-react";
import { aiInsights } from "../data/advancedData";
import { Progress } from "../components/ui/progress";

export function AIInsights() {
  const getInsightIcon = (type: string) => {
    switch (type) {
      case "prediction":
        return <Brain className="w-5 h-5 text-purple-400" />;
      case "pattern":
        return <Target className="w-5 h-5 text-blue-400" />;
      case "anomaly":
        return <AlertTriangle className="w-5 h-5 text-yellow-400" />;
      case "opportunity":
        return <Lightbulb className="w-5 h-5 text-green-400" />;
      default:
        return <TrendingUp className="w-5 h-5 text-slate-400" />;
    }
  };

  const getActionColor = (action: string) => {
    switch (action) {
      case "buy":
        return "bg-green-600/20 text-green-400 border-green-600/30";
      case "sell":
        return "bg-red-600/20 text-red-400 border-red-600/30";
      default:
        return "bg-slate-600/20 text-slate-400 border-slate-600/30";
    }
  };

  const getConfidenceColor = (confidence: number) => {
    if (confidence >= 80) return "text-green-400";
    if (confidence >= 60) return "text-yellow-400";
    return "text-red-400";
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">AI Market Insights</h2>
        <p className="text-slate-400 mt-1">Machine learning powered predictions and opportunities</p>
      </div>

      {/* Summary Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-gradient-to-br from-purple-900/20 to-purple-800/10 border-purple-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-purple-400">Active Insights</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{aiInsights.length}</div>
            <p className="text-sm text-purple-400 mt-1">Last 24 hours</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Avg Confidence</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {(aiInsights.reduce((sum, i) => sum + i.confidence, 0) / aiInsights.length).toFixed(1)}%
            </div>
            <p className="text-sm text-slate-400 mt-1">Across all insights</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Buy Signals</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-green-400">
              {aiInsights.filter(i => i.action === "buy").length}
            </div>
            <p className="text-sm text-slate-400 mt-1">Opportunities detected</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Potential Gain</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {aiInsights.reduce((sum, i) => sum + (i.potentialGain || 0), 0).toFixed(1)}%
            </div>
            <p className="text-sm text-slate-400 mt-1">Combined upside</p>
          </CardContent>
        </Card>
      </div>

      {/* High Priority Insights */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">High Confidence Insights</CardTitle>
          </div>
          <p className="text-sm text-slate-400">AI predictions with 75%+ confidence</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {aiInsights.filter(insight => insight.confidence >= 75).map((insight) => (
              <div
                key={insight.id}
                className="p-5 bg-slate-900/50 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-start gap-4">
                  <div className="p-3 bg-slate-800 rounded-lg">
                    {getInsightIcon(insight.type)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4 mb-2">
                      <div>
                        <h3 className="text-white font-semibold text-lg">{insight.title}</h3>
                        <div className="flex items-center gap-2 mt-1">
                          <Badge variant="outline" className="border-slate-700 text-slate-400 text-xs">
                            {insight.type}
                          </Badge>
                          <Badge className={getActionColor(insight.action)}>
                            {insight.action.toUpperCase()}
                          </Badge>
                          {insight.relatedSymbols.map((symbol) => (
                            <span key={symbol} className="px-2 py-0.5 bg-blue-600/20 text-blue-400 rounded text-xs font-mono">
                              {symbol}
                            </span>
                          ))}
                        </div>
                      </div>
                      <div className="text-right">
                        <div className={`text-2xl font-semibold ${getConfidenceColor(insight.confidence)}`}>
                          {insight.confidence}%
                        </div>
                        <div className="text-xs text-slate-500">confidence</div>
                      </div>
                    </div>

                    <p className="text-slate-300 mb-4">{insight.description}</p>

                    <div className="grid grid-cols-2 gap-4 p-4 bg-slate-800/50 rounded-lg mb-4">
                      {insight.currentPrice && insight.targetPrice && (
                        <>
                          <div>
                            <div className="text-sm text-slate-400">Current Price</div>
                            <div className="text-white font-semibold">${insight.currentPrice.toFixed(2)}</div>
                          </div>
                          <div>
                            <div className="text-sm text-slate-400">Target Price</div>
                            <div className="text-green-400 font-semibold">${insight.targetPrice.toFixed(2)}</div>
                          </div>
                          <div>
                            <div className="text-sm text-slate-400">Potential Gain</div>
                            <div className="text-green-400 font-semibold">+{insight.potentialGain?.toFixed(2)}%</div>
                          </div>
                        </>
                      )}
                      <div>
                        <div className="text-sm text-slate-400">Timeframe</div>
                        <div className="text-white font-semibold">{insight.timeframe}</div>
                      </div>
                    </div>

                    <div className="flex items-center justify-between">
                      <div className="text-xs text-slate-500">
                        Generated: {new Date(insight.timestamp).toLocaleString()}
                      </div>
                      <Button size="sm" className="bg-blue-600 hover:bg-blue-700">
                        View Details
                        <ArrowRight className="w-4 h-4 ml-2" />
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* All Insights */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">All AI Insights</CardTitle>
          <p className="text-sm text-slate-400">Complete market analysis feed</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {aiInsights.map((insight) => (
              <div
                key={insight.id}
                className="p-4 bg-slate-800/30 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-start gap-3">
                  <div className="p-2 bg-slate-800 rounded-lg">
                    {getInsightIcon(insight.type)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-4">
                      <div className="flex-1">
                        <h4 className="text-white font-medium">{insight.title}</h4>
                        <p className="text-sm text-slate-400 mt-1">{insight.description}</p>
                        <div className="flex items-center gap-2 mt-2">
                          <Badge className={getActionColor(insight.action)} >
                            {insight.action}
                          </Badge>
                          <span className="text-xs text-slate-500">{insight.timeframe}</span>
                        </div>
                      </div>
                      <div className="text-right min-w-[100px]">
                        <div className="text-lg font-semibold text-white mb-1">
                          {insight.confidence}%
                        </div>
                        <Progress value={insight.confidence} className="h-2" />
                        <div className="text-xs text-slate-500 mt-1">confidence</div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Model Performance */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">AI Model Performance</CardTitle>
          <p className="text-sm text-slate-400">Historical accuracy metrics</p>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-800/50 rounded-lg">
              <div className="text-sm text-slate-400 mb-2">Pattern Recognition</div>
              <div className="text-2xl text-white mb-2">87.3%</div>
              <Progress value={87.3} className="h-2" />
              <p className="text-xs text-slate-500 mt-2">1,234 predictions analyzed</p>
            </div>
            <div className="p-4 bg-slate-800/50 rounded-lg">
              <div className="text-sm text-slate-400 mb-2">Price Predictions</div>
              <div className="text-2xl text-white mb-2">72.8%</div>
              <Progress value={72.8} className="h-2" />
              <p className="text-xs text-slate-500 mt-2">892 predictions analyzed</p>
            </div>
            <div className="p-4 bg-slate-800/50 rounded-lg">
              <div className="text-sm text-slate-400 mb-2">Anomaly Detection</div>
              <div className="text-2xl text-white mb-2">91.5%</div>
              <Progress value={91.5} className="h-2" />
              <p className="text-xs text-slate-500 mt-2">567 events detected</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
