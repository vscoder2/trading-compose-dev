import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Bell, Plus, Trash2, CheckCircle, Clock } from "lucide-react";
import { alerts } from "../data/optionsData";
import { toast } from "sonner";

export function AlertManagement() {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [newAlert, setNewAlert] = useState({
    symbol: "",
    type: "price",
    condition: "above",
    value: "",
  });

  const handleCreateAlert = () => {
    if (!newAlert.symbol || !newAlert.value) {
      toast.error("Please fill in all fields");
      return;
    }
    toast.success(`Alert created for ${newAlert.symbol}`);
    setShowCreateForm(false);
    setNewAlert({ symbol: "", type: "price", condition: "above", value: "" });
  };

  const handleDeleteAlert = (id: string) => {
    toast.success("Alert deleted");
  };

  const getStatusColor = (status: string) => {
    switch (status) {
      case "active":
        return "bg-blue-600/20 text-blue-400";
      case "triggered":
        return "bg-green-600/20 text-green-400";
      case "expired":
        return "bg-slate-600/20 text-slate-400";
      default:
        return "bg-slate-600/20 text-slate-400";
    }
  };

  const getTypeColor = (type: string) => {
    switch (type) {
      case "price":
        return "bg-purple-600/20 text-purple-400";
      case "indicator":
        return "bg-blue-600/20 text-blue-400";
      case "volume":
        return "bg-yellow-600/20 text-yellow-400";
      case "news":
        return "bg-green-600/20 text-green-400";
      case "options":
        return "bg-orange-600/20 text-orange-400";
      default:
        return "bg-slate-600/20 text-slate-400";
    }
  };

  const activeAlerts = alerts.filter((a) => a.status === "active");
  const triggeredAlerts = alerts.filter((a) => a.status === "triggered");

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl text-white font-semibold">Alert Management</h2>
          <p className="text-slate-400 mt-1">Set up custom alerts for price, indicators, and events</p>
        </div>
        <Button onClick={() => setShowCreateForm(!showCreateForm)} className="bg-blue-600 hover:bg-blue-700">
          <Plus className="w-4 h-4 mr-2" />
          Create Alert
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{alerts.length}</div>
            <p className="text-sm text-slate-400 mt-1">All configured</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-blue-900/20 to-blue-800/10 border-blue-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-blue-400">Active Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{activeAlerts.length}</div>
            <p className="text-sm text-blue-400 mt-1">Monitoring</p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Triggered Today</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{triggeredAlerts.length}</div>
            <p className="text-sm text-green-400 mt-1">Notifications sent</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Alert Types</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">5</div>
            <p className="text-sm text-slate-400 mt-1">Categories available</p>
          </CardContent>
        </Card>
      </div>

      {/* Create Alert Form */}
      {showCreateForm && (
        <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
          <CardHeader>
            <CardTitle className="text-white">Create New Alert</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <Label className="text-slate-300">Symbol</Label>
                <Input
                  placeholder="AAPL"
                  value={newAlert.symbol}
                  onChange={(e) => setNewAlert({ ...newAlert, symbol: e.target.value.toUpperCase() })}
                  className="bg-slate-800 border-slate-700 text-white mt-1"
                />
              </div>

              <div>
                <Label className="text-slate-300">Alert Type</Label>
                <Select value={newAlert.type} onValueChange={(value) => setNewAlert({ ...newAlert, type: value })}>
                  <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-800 border-slate-700">
                    <SelectItem value="price" className="text-white">
                      Price
                    </SelectItem>
                    <SelectItem value="indicator" className="text-white">
                      Technical Indicator
                    </SelectItem>
                    <SelectItem value="volume" className="text-white">
                      Volume
                    </SelectItem>
                    <SelectItem value="news" className="text-white">
                      News
                    </SelectItem>
                    <SelectItem value="options" className="text-white">
                      Options
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div>
                <Label className="text-slate-300">Condition</Label>
                <Select
                  value={newAlert.condition}
                  onValueChange={(value) => setNewAlert({ ...newAlert, condition: value })}
                >
                  <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="bg-slate-800 border-slate-700">
                    <SelectItem value="above" className="text-white">
                      Above
                    </SelectItem>
                    <SelectItem value="below" className="text-white">
                      Below
                    </SelectItem>
                    <SelectItem value="equals" className="text-white">
                      Equals
                    </SelectItem>
                    <SelectItem value="crosses" className="text-white">
                      Crosses
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div>
                <Label className="text-slate-300">Target Value</Label>
                <Input
                  placeholder="180.00"
                  value={newAlert.value}
                  onChange={(e) => setNewAlert({ ...newAlert, value: e.target.value })}
                  className="bg-slate-800 border-slate-700 text-white mt-1"
                />
              </div>
            </div>

            <div className="flex gap-3 mt-6">
              <Button onClick={handleCreateAlert} className="bg-blue-600 hover:bg-blue-700">
                <Plus className="w-4 h-4 mr-2" />
                Create Alert
              </Button>
              <Button
                onClick={() => setShowCreateForm(false)}
                variant="outline"
                className="border-slate-700 text-slate-300 hover:bg-slate-800"
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Active Alerts */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Clock className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Active Alerts</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Currently monitoring these conditions</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {activeAlerts.map((alert) => (
              <div
                key={alert.id}
                className="p-4 bg-slate-800/30 rounded-lg border border-slate-700 hover:border-slate-600 transition-colors"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-white font-mono font-semibold text-lg">{alert.symbol}</span>
                      <Badge className={getTypeColor(alert.type)}>{alert.type}</Badge>
                      <Badge className={getStatusColor(alert.status)}>{alert.status}</Badge>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-3">
                      <div>
                        <div className="text-xs text-slate-400">Condition</div>
                        <div className="text-white font-medium">{alert.condition}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Target Value</div>
                        <div className="text-white font-medium">{alert.targetValue}</div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Current Value</div>
                        <div className="text-slate-300 font-medium">{alert.currentValue}</div>
                      </div>
                    </div>

                    <div className="text-xs text-slate-500 mt-3">
                      Created: {new Date(alert.createdAt).toLocaleString()}
                    </div>
                  </div>

                  <Button
                    onClick={() => handleDeleteAlert(alert.id)}
                    size="sm"
                    variant="outline"
                    className="border-slate-700 text-red-400 hover:bg-red-900/20"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Triggered Alerts */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CheckCircle className="w-5 h-5 text-green-400" />
            <CardTitle className="text-white">Triggered Alerts</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Recently triggered notifications</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {triggeredAlerts.map((alert) => (
              <div
                key={alert.id}
                className="p-4 bg-green-950/20 rounded-lg border border-green-800/30"
              >
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <span className="text-white font-mono font-semibold text-lg">{alert.symbol}</span>
                      <Badge className={getTypeColor(alert.type)}>{alert.type}</Badge>
                      <Badge className="bg-green-600/20 text-green-400">Triggered</Badge>
                    </div>

                    <div className="text-sm text-green-400 mb-2">
                      Alert triggered: {alert.condition} {alert.targetValue}
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <div className="text-xs text-slate-400">Triggered At</div>
                        <div className="text-white font-medium">
                          {alert.triggeredAt && new Date(alert.triggeredAt).toLocaleString()}
                        </div>
                      </div>
                      <div>
                        <div className="text-xs text-slate-400">Current Value</div>
                        <div className="text-green-400 font-medium">{alert.currentValue}</div>
                      </div>
                    </div>
                  </div>

                  <Button
                    onClick={() => handleDeleteAlert(alert.id)}
                    size="sm"
                    variant="outline"
                    className="border-slate-700 text-slate-300 hover:bg-slate-800"
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Alert Types Guide */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">Alert Types</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-5 gap-4">
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-purple-600/20 text-purple-400 mb-2">Price</Badge>
              <p className="text-sm text-slate-400">Get notified when a stock reaches a specific price level</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-blue-600/20 text-blue-400 mb-2">Indicator</Badge>
              <p className="text-sm text-slate-400">Alerts based on RSI, MACD, moving averages, etc.</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-yellow-600/20 text-yellow-400 mb-2">Volume</Badge>
              <p className="text-sm text-slate-400">Unusual volume spikes or specific volume thresholds</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-green-600/20 text-green-400 mb-2">News</Badge>
              <p className="text-sm text-slate-400">Breaking news and important announcements</p>
            </div>
            <div className="p-4 bg-slate-900/50 rounded-lg">
              <Badge className="bg-orange-600/20 text-orange-400 mb-2">Options</Badge>
              <p className="text-sm text-slate-400">Unusual options activity and flow alerts</p>
            </div>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
