import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { Newspaper, TrendingUp, TrendingDown, Minus } from "lucide-react";
import { mockNews } from "../data/newsData";

export function NewsFeed() {
  const getSentimentIcon = (sentiment: string) => {
    switch (sentiment) {
      case "positive":
        return <TrendingUp className="w-4 h-4 text-green-400" />;
      case "negative":
        return <TrendingDown className="w-4 h-4 text-red-400" />;
      default:
        return <Minus className="w-4 h-4 text-slate-400" />;
    }
  };

  const getSentimentColor = (sentiment: string) => {
    switch (sentiment) {
      case "positive":
        return "bg-green-600/20 text-green-400 border-green-600/30";
      case "negative":
        return "bg-red-600/20 text-red-400 border-red-600/30";
      default:
        return "bg-slate-600/20 text-slate-400 border-slate-600/30";
    }
  };

  const getImpactColor = (impact: string) => {
    switch (impact) {
      case "high":
        return "bg-purple-600/20 text-purple-400";
      case "medium":
        return "bg-blue-600/20 text-blue-400";
      default:
        return "bg-slate-600/20 text-slate-400";
    }
  };

  return (
    <Card className="bg-slate-900 border-slate-800">
      <CardHeader>
        <div className="flex items-center gap-2">
          <Newspaper className="w-5 h-5 text-blue-400" />
          <CardTitle className="text-white">Market News</CardTitle>
        </div>
        <p className="text-sm text-slate-400">Latest market updates</p>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {mockNews.map((news) => (
            <div
              key={news.id}
              className="p-4 bg-slate-800/30 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors cursor-pointer"
            >
              <div className="flex items-start gap-3">
                <div className={`p-2 rounded-lg border ${getSentimentColor(news.sentiment)}`}>
                  {getSentimentIcon(news.sentiment)}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-2 mb-2">
                    <h4 className="text-white font-medium line-clamp-2">{news.title}</h4>
                    <Badge className={getImpactColor(news.impact)}>
                      {news.impact}
                    </Badge>
                  </div>
                  <p className="text-sm text-slate-400 mb-3">{news.summary}</p>
                  <div className="flex items-center gap-3 text-xs text-slate-500">
                    <span>{news.source}</span>
                    <span>•</span>
                    <span>{new Date(news.timestamp).toLocaleTimeString()}</span>
                    {news.relatedStocks.length > 0 && (
                      <>
                        <span>•</span>
                        <div className="flex gap-1">
                          {news.relatedStocks.map((stock) => (
                            <span key={stock} className="px-2 py-0.5 bg-blue-600/20 text-blue-400 rounded font-mono">
                              {stock}
                            </span>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
          {mockNews.length === 0 && (
            <div className="text-center py-8 text-slate-500">No runtime news/events available.</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
