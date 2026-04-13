import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Calendar, Clock, TrendingUp, AlertCircle } from "lucide-react";
import { economicCalendar } from "../data/advancedData";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";

export function EconomicCalendar() {
  const getImpactColor = (impact: string) => {
    switch (impact) {
      case "high":
        return "bg-red-600/20 text-red-400 border-red-600/30";
      case "medium":
        return "bg-yellow-600/20 text-yellow-400 border-yellow-600/30";
      default:
        return "bg-green-600/20 text-green-400 border-green-600/30";
    }
  };

  const groupEventsByDate = () => {
    const grouped: Record<string, typeof economicCalendar> = {};
    economicCalendar.forEach((event) => {
      if (!grouped[event.date]) {
        grouped[event.date] = [];
      }
      grouped[event.date].push(event);
    });
    return grouped;
  };

  const groupedEvents = groupEventsByDate();
  const today = "2026-04-05";
  const upcomingEvents = economicCalendar.filter(e => e.date >= today);
  const highImpactEvents = economicCalendar.filter(e => e.impact === "high");

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Economic Calendar</h2>
        <p className="text-slate-400 mt-1">Track important economic events and earnings</p>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Today's Events</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">
              {economicCalendar.filter(e => e.date === today).length}
            </div>
            <p className="text-sm text-slate-400 mt-1">Scheduled releases</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-red-900/20 to-red-800/10 border-red-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-red-400">High Impact</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{highImpactEvents.length}</div>
            <p className="text-sm text-red-400 mt-1">Critical events this week</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Upcoming</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{upcomingEvents.length}</div>
            <p className="text-sm text-slate-400 mt-1">Events this week</p>
          </CardContent>
        </Card>
      </div>

      {/* High Impact Events Alert */}
      <Card className="bg-gradient-to-br from-yellow-900/20 to-orange-900/20 border-yellow-800/50">
        <CardHeader>
          <div className="flex items-center gap-2">
            <AlertCircle className="w-5 h-5 text-yellow-400" />
            <CardTitle className="text-white">High Impact Events Today</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {economicCalendar
              .filter(e => e.date === today && e.impact === "high")
              .map((event) => (
                <div
                  key={event.id}
                  className="p-4 bg-slate-900/50 rounded-lg border border-yellow-600/30 flex items-center gap-4"
                >
                  <div className="p-3 bg-yellow-600/20 rounded-lg">
                    <Clock className="w-5 h-5 text-yellow-400" />
                  </div>
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <h4 className="text-white font-semibold">{event.event}</h4>
                      <Badge className={getImpactColor(event.impact)}>
                        {event.impact} impact
                      </Badge>
                    </div>
                    <div className="flex items-center gap-4 text-sm text-slate-400">
                      <span>{event.time} EST</span>
                      <span>•</span>
                      <span>{event.currency}</span>
                      {event.forecast && (
                        <>
                          <span>•</span>
                          <span>Forecast: {event.forecast}</span>
                        </>
                      )}
                      {event.previous && (
                        <>
                          <span>•</span>
                          <span>Previous: {event.previous}</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
              ))}
          </div>
        </CardContent>
      </Card>

      {/* Calendar Tabs */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Calendar className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Event Calendar</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <Tabs defaultValue="all" className="w-full">
            <TabsList className="grid w-full md:w-auto grid-cols-4 bg-slate-800">
              <TabsTrigger value="all">All Events</TabsTrigger>
              <TabsTrigger value="economic">Economic</TabsTrigger>
              <TabsTrigger value="earnings">Earnings</TabsTrigger>
              <TabsTrigger value="high">High Impact</TabsTrigger>
            </TabsList>

            <TabsContent value="all" className="mt-6">
              <div className="space-y-6">
                {Object.entries(groupedEvents).map(([date, events]) => (
                  <div key={date}>
                    <div className="flex items-center gap-3 mb-4">
                      <div className={`px-3 py-1 rounded-lg ${
                        date === today ? "bg-blue-600/20 text-blue-400" : "bg-slate-800 text-slate-400"
                      }`}>
                        <div className="text-xs">
                          {new Date(date).toLocaleDateString("en-US", { weekday: "short" })}
                        </div>
                        <div className="font-semibold">
                          {new Date(date).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        </div>
                      </div>
                      {date === today && (
                        <Badge className="bg-blue-600/20 text-blue-400">Today</Badge>
                      )}
                    </div>
                    <div className="space-y-3">
                      {events.map((event) => (
                        <div
                          key={event.id}
                          className="p-4 bg-slate-800/30 rounded-lg border border-slate-700/50 hover:border-slate-600 transition-colors"
                        >
                          <div className="flex items-start justify-between">
                            <div className="flex-1">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="text-white font-medium">{event.event}</span>
                                <Badge className={getImpactColor(event.impact)}>
                                  {event.impact}
                                </Badge>
                              </div>
                              <div className="flex items-center gap-4 text-sm text-slate-400">
                                <div className="flex items-center gap-1">
                                  <Clock className="w-4 h-4" />
                                  <span>{event.time}</span>
                                </div>
                                <span>{event.currency}</span>
                              </div>
                            </div>
                            <div className="text-right min-w-[200px]">
                              {event.forecast && (
                                <div className="text-sm">
                                  <span className="text-slate-400">Forecast: </span>
                                  <span className="text-white font-medium">{event.forecast}</span>
                                </div>
                              )}
                              {event.previous && (
                                <div className="text-sm">
                                  <span className="text-slate-400">Previous: </span>
                                  <span className="text-slate-300">{event.previous}</span>
                                </div>
                              )}
                              {event.actual && (
                                <div className="text-sm">
                                  <span className="text-slate-400">Actual: </span>
                                  <span className="text-green-400 font-medium">{event.actual}</span>
                                </div>
                              )}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>

            <TabsContent value="economic" className="mt-6">
              <div className="space-y-3">
                {economicCalendar
                  .filter(e => !e.event.includes("Earnings"))
                  .map((event) => (
                    <div
                      key={event.id}
                      className="p-4 bg-slate-800/30 rounded-lg border border-slate-700/50"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <h4 className="text-white font-medium mb-1">{event.event}</h4>
                          <div className="flex items-center gap-3 text-sm text-slate-400">
                            <span>{event.date}</span>
                            <span>•</span>
                            <span>{event.time}</span>
                          </div>
                        </div>
                        <Badge className={getImpactColor(event.impact)}>
                          {event.impact}
                        </Badge>
                      </div>
                    </div>
                  ))}
              </div>
            </TabsContent>

            <TabsContent value="earnings" className="mt-6">
              <div className="space-y-3">
                {economicCalendar
                  .filter(e => e.event.includes("Earnings"))
                  .map((event) => (
                    <div
                      key={event.id}
                      className="p-4 bg-slate-800/30 rounded-lg border border-slate-700/50"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <h4 className="text-white font-medium mb-1">{event.event}</h4>
                          <div className="flex items-center gap-3 text-sm text-slate-400">
                            <span>{event.date}</span>
                            <span>•</span>
                            <span>{event.time}</span>
                            {event.forecast && (
                              <>
                                <span>•</span>
                                <span>EPS Forecast: {event.forecast}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <Badge className={getImpactColor(event.impact)}>
                          {event.impact}
                        </Badge>
                      </div>
                    </div>
                  ))}
              </div>
            </TabsContent>

            <TabsContent value="high" className="mt-6">
              <div className="space-y-3">
                {highImpactEvents.map((event) => (
                  <div
                    key={event.id}
                    className="p-4 bg-slate-800/30 rounded-lg border border-red-600/30"
                  >
                    <div className="flex items-center justify-between">
                      <div>
                        <h4 className="text-white font-medium mb-1">{event.event}</h4>
                        <div className="flex items-center gap-3 text-sm text-slate-400">
                          <span>{event.date}</span>
                          <span>•</span>
                          <span>{event.time}</span>
                          <span>•</span>
                          <span>{event.currency}</span>
                        </div>
                      </div>
                      <Badge className={getImpactColor(event.impact)}>
                        {event.impact} impact
                      </Badge>
                    </div>
                  </div>
                ))}
              </div>
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Info Card */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">Market Impact Guide</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-red-600/20 text-red-400 mb-2">High Impact</Badge>
              <p className="text-sm text-slate-400">
                Major market-moving events. Expect significant volatility and price movements.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-yellow-600/20 text-yellow-400 mb-2">Medium Impact</Badge>
              <p className="text-sm text-slate-400">
                Moderate influence on markets. Can cause sector-specific movements.
              </p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-green-600/20 text-green-400 mb-2">Low Impact</Badge>
              <p className="text-sm text-slate-400">
                Limited market influence. Usually affects specific stocks or minor price adjustments.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
