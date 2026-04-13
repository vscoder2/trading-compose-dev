import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Progress } from "../components/ui/progress";
import { TrendingUp, TrendingDown, MessageSquare, Newspaper, Users } from "lucide-react";
import { sentimentData } from "../data/optionsData";
import { PieChart, Pie, Cell, ResponsiveContainer, Legend, Tooltip, BarChart, Bar, XAxis, YAxis, CartesianGrid } from "recharts";

export function MarketSentiment() {
  const getSentimentColor = (score: number) => {
    if (score >= 70) return "text-green-400";
    if (score >= 50) return "text-yellow-400";
    return "text-red-400";
  };

  const getSentimentBg = (score: number) => {
    if (score >= 70) return "bg-green-600/20";
    if (score >= 50) return "bg-yellow-600/20";
    return "bg-red-600/20";
  };

  const getSentimentLabel = (score: number) => {
    if (score >= 80) return "Very Bullish";
    if (score >= 70) return "Bullish";
    if (score >= 50) return "Neutral";
    if (score >= 30) return "Bearish";
    return "Very Bearish";
  };

  const marketSentiment = [
    { name: "Bullish", value: 42, color: "#10b981" },
    { name: "Neutral", value: 35, color: "#f59e0b" },
    { name: "Bearish", value: 23, color: "#ef4444" },
  ];

  const sentimentTrends = sentimentData.map((s) => ({
    symbol: s.symbol,
    sentiment: s.overallScore,
  }));

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Market Sentiment Analysis</h2>
        <p className="text-slate-400 mt-1">AI-powered sentiment from news, social media, and analyst reports</p>
      </div>

      {/* Overall Market Sentiment */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <CardTitle className="text-white">Overall Market Sentiment</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={marketSentiment}
                  cx="50%"
                  cy="50%"
                  labelLine={false}
                  label={({ name, value }) => `${name} ${value}%`}
                  outerRadius={80}
                  fill="#8884d8"
                  dataKey="value"
                >
                  {marketSentiment.map((entry, index) => (
                    <Cell key={`cell-${index}`} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: "8px",
                  }}
                />
                <Legend />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader>
            <CardTitle className="text-white">Sentiment by Stock</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={250}>
              <BarChart data={sentimentTrends}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="symbol" stroke="#64748b" />
                <YAxis stroke="#64748b" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "#1e293b",
                    border: "1px solid #334155",
                    borderRadius: "8px",
                    color: "#fff",
                  }}
                />
                <Bar dataKey="sentiment" fill="#3b82f6" radius={[8, 8, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      {/* Sentiment Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-5 h-5 text-green-400" />
              <CardTitle className="text-sm text-green-400">Bullish Stocks</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {sentimentData.filter((s) => s.overallScore >= 70).length}
            </div>
            <p className="text-sm text-green-400 mt-1">Strong positive sentiment</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-blue-400" />
              <CardTitle className="text-sm text-slate-400">Social Mentions</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {sentimentData.reduce((sum, s) => sum + s.mentions24h, 0).toLocaleString()}
            </div>
            <p className="text-sm text-slate-400 mt-1">Last 24 hours</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-red-900/20 to-red-800/10 border-red-800/50">
          <CardHeader className="pb-2">
            <div className="flex items-center gap-2">
              <TrendingDown className="w-5 h-5 text-red-400" />
              <CardTitle className="text-sm text-red-400">Bearish Stocks</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {sentimentData.filter((s) => s.overallScore < 50).length}
            </div>
            <p className="text-sm text-red-400 mt-1">Negative sentiment</p>
          </CardContent>
        </Card>
      </div>

      {/* Detailed Sentiment Analysis */}
      <div className="grid grid-cols-1 gap-4">
        {sentimentData.map((stock) => (
          <Card key={stock.symbol} className="bg-slate-900 border-slate-800">
            <CardHeader>
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-white font-mono font-semibold text-xl">{stock.symbol}</h3>
                  <div className="flex items-center gap-2 mt-2">
                    <Badge className={getSentimentBg(stock.overallScore)}>
                      <span className={getSentimentColor(stock.overallScore)}>
                        {getSentimentLabel(stock.overallScore)}
                      </span>
                    </Badge>
                    <Badge
                      className={
                        stock.trend === "improving"
                          ? "bg-green-600/20 text-green-400"
                          : stock.trend === "declining"
                          ? "bg-red-600/20 text-red-400"
                          : "bg-slate-600/20 text-slate-400"
                      }
                    >
                      {stock.trend === "improving" ? "↗" : stock.trend === "declining" ? "↘" : "→"}{" "}
                      {stock.trend}
                    </Badge>
                  </div>
                </div>
                <div className="text-right">
                  <div className={`text-4xl font-bold ${getSentimentColor(stock.overallScore)}`}>
                    {stock.overallScore}
                  </div>
                  <div className="text-xs text-slate-400 mt-1">Overall Score</div>
                </div>
              </div>
            </CardHeader>

            <CardContent>
              <div className="space-y-4">
                {/* News Sentiment */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Newspaper className="w-4 h-4 text-blue-400" />
                      <span className="text-sm text-slate-400">News Sentiment</span>
                    </div>
                    <span className={`font-semibold ${getSentimentColor(stock.newsScore)}`}>
                      {stock.newsScore}
                    </span>
                  </div>
                  <Progress value={stock.newsScore} className="h-2" />
                </div>

                {/* Social Media Sentiment */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <MessageSquare className="w-4 h-4 text-purple-400" />
                      <span className="text-sm text-slate-400">Social Media Sentiment</span>
                    </div>
                    <span className={`font-semibold ${getSentimentColor(stock.socialScore)}`}>
                      {stock.socialScore}
                    </span>
                  </div>
                  <Progress value={stock.socialScore} className="h-2" />
                </div>

                {/* Analyst Sentiment */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Users className="w-4 h-4 text-green-400" />
                      <span className="text-sm text-slate-400">Analyst Sentiment</span>
                    </div>
                    <span className={`font-semibold ${getSentimentColor(stock.analystScore)}`}>
                      {stock.analystScore}
                    </span>
                  </div>
                  <Progress value={stock.analystScore} className="h-2" />
                </div>

                {/* Stats */}
                <div className="grid grid-cols-2 gap-4 pt-4 border-t border-slate-700">
                  <div className="p-3 bg-slate-800/30 rounded-lg">
                    <div className="text-xs text-slate-400">Mentions (24h)</div>
                    <div className="text-lg text-white font-semibold">
                      {stock.mentions24h.toLocaleString()}
                    </div>
                  </div>
                  <div className="p-3 bg-slate-800/30 rounded-lg">
                    <div className="text-xs text-slate-400">Total Volume</div>
                    <div className="text-lg text-white font-semibold">
                      {stock.volume.toLocaleString()}
                    </div>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Sentiment Info */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">How Sentiment Analysis Works</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="flex items-center gap-2 mb-2">
                <Newspaper className="w-5 h-5 text-blue-400" />
                <h4 className="text-white font-semibold">News Analysis</h4>
              </div>
              <p className="text-sm text-slate-400">
                AI analyzes thousands of news articles, press releases, and financial reports using natural language processing.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="flex items-center gap-2 mb-2">
                <MessageSquare className="w-5 h-5 text-purple-400" />
                <h4 className="text-white font-semibold">Social Monitoring</h4>
              </div>
              <p className="text-sm text-slate-400">
                Tracks sentiment from social media platforms, forums, and trading communities in real-time.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <div className="flex items-center gap-2 mb-2">
                <Users className="w-5 h-5 text-green-400" />
                <h4 className="text-white font-semibold">Analyst Ratings</h4>
              </div>
              <p className="text-sm text-slate-400">
                Aggregates buy/sell/hold ratings from professional analysts at major financial institutions.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
