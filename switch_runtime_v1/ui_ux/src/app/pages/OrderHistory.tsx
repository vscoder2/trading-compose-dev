import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../components/ui/select";
import { Search, Filter, Download, ArrowUpRight, ArrowDownRight } from "lucide-react";
import { orderHistory } from "../data/advancedData";
import { useState } from "react";

export function OrderHistory() {
  const [searchQuery, setSearchQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState("all");
  const [filterType, setFilterType] = useState("all");

  const filteredOrders = orderHistory.filter(order => {
    const matchesSearch = order.symbol.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesStatus = filterStatus === "all" || order.status === filterStatus;
    const matchesType = filterType === "all" || order.type === filterType;
    return matchesSearch && matchesStatus && matchesType;
  });

  const getStatusColor = (status: string) => {
    switch (status) {
      case "filled":
        return "bg-green-600/20 text-green-400";
      case "pending":
        return "bg-yellow-600/20 text-yellow-400";
      case "cancelled":
        return "bg-red-600/20 text-red-400";
      case "partial":
        return "bg-blue-600/20 text-blue-400";
      default:
        return "bg-slate-600/20 text-slate-400";
    }
  };

  const totalFilled = orderHistory.filter(o => o.status === "filled").length;
  const totalPending = orderHistory.filter(o => o.status === "pending").length;
  const totalVolume = orderHistory
    .filter(o => o.status === "filled")
    .reduce((sum, o) => sum + o.total, 0);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Order History</h2>
        <p className="text-slate-400 mt-1">View and manage your trading orders</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Orders</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">{orderHistory.length}</div>
            <p className="text-sm text-slate-400 mt-1">All time</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Filled Orders</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-green-400">{totalFilled}</div>
            <p className="text-sm text-slate-400 mt-1">Successfully executed</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Pending Orders</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-yellow-400">{totalPending}</div>
            <p className="text-sm text-slate-400 mt-1">Awaiting execution</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Total Volume</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">${totalVolume.toLocaleString()}</div>
            <p className="text-sm text-slate-400 mt-1">Filled orders</p>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Filter Orders</CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
              <Input
                placeholder="Search by symbol..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-10 bg-slate-800 border-slate-700 text-white"
              />
            </div>

            <Select value={filterStatus} onValueChange={setFilterStatus}>
              <SelectTrigger className="bg-slate-800 border-slate-700 text-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-slate-800 border-slate-700">
                <SelectItem value="all" className="text-white">All Status</SelectItem>
                <SelectItem value="filled" className="text-white">Filled</SelectItem>
                <SelectItem value="pending" className="text-white">Pending</SelectItem>
                <SelectItem value="cancelled" className="text-white">Cancelled</SelectItem>
                <SelectItem value="partial" className="text-white">Partial</SelectItem>
              </SelectContent>
            </Select>

            <Select value={filterType} onValueChange={setFilterType}>
              <SelectTrigger className="bg-slate-800 border-slate-700 text-white">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-slate-800 border-slate-700">
                <SelectItem value="all" className="text-white">All Types</SelectItem>
                <SelectItem value="buy" className="text-white">Buy</SelectItem>
                <SelectItem value="sell" className="text-white">Sell</SelectItem>
              </SelectContent>
            </Select>

            <Button variant="outline" className="border-slate-700 text-slate-300 hover:bg-slate-800">
              <Download className="w-4 h-4 mr-2" />
              Export
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Orders Table */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">
            Orders ({filteredOrders.length})
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-slate-800">
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Order ID</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Symbol</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Type</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Order Type</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Shares</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Price</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Total</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Status</th>
                  <th className="text-left py-3 px-4 text-sm text-slate-400">Date/Time</th>
                  <th className="text-right py-3 px-4 text-sm text-slate-400">Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredOrders.map((order) => (
                  <tr key={order.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="py-3 px-4">
                      <span className="text-slate-300 font-mono text-sm">{order.id}</span>
                    </td>
                    <td className="py-3 px-4">
                      <span className="text-white font-mono font-semibold">{order.symbol}</span>
                    </td>
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1">
                        {order.type === "buy" ? (
                          <>
                            <ArrowUpRight className="w-4 h-4 text-green-500" />
                            <span className="text-green-400 font-medium">BUY</span>
                          </>
                        ) : (
                          <>
                            <ArrowDownRight className="w-4 h-4 text-red-500" />
                            <span className="text-red-400 font-medium">SELL</span>
                          </>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <Badge variant="outline" className="border-slate-700 text-slate-300 capitalize">
                        {order.orderType}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-right text-white">{order.shares}</td>
                    <td className="py-3 px-4 text-right text-white">${order.price.toFixed(2)}</td>
                    <td className="py-3 px-4 text-right text-white">${order.total.toLocaleString()}</td>
                    <td className="py-3 px-4">
                      <Badge className={getStatusColor(order.status)}>
                        {order.status}
                        {order.status === "partial" && order.fillRate && ` (${order.fillRate}%)`}
                      </Badge>
                    </td>
                    <td className="py-3 px-4 text-slate-300 text-sm">
                      {new Date(order.timestamp).toLocaleString()}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {order.status === "pending" && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="border-slate-700 text-red-400 hover:bg-red-900/20"
                        >
                          Cancel
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      {/* Recent Activity */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <CardTitle className="text-white">Recent Activity</CardTitle>
          <p className="text-sm text-slate-400">Latest order executions</p>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {orderHistory.slice(0, 5).map((order) => (
              <div
                key={order.id}
                className="flex items-center justify-between p-4 bg-slate-800/30 rounded-lg"
              >
                <div className="flex items-center gap-4">
                  <div className={`p-2 rounded-lg ${
                    order.type === "buy" ? "bg-green-600/20" : "bg-red-600/20"
                  }`}>
                    {order.type === "buy" ? (
                      <ArrowUpRight className="w-5 h-5 text-green-400" />
                    ) : (
                      <ArrowDownRight className="w-5 h-5 text-red-400" />
                    )}
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-white font-mono font-semibold">{order.symbol}</span>
                      <Badge className={getStatusColor(order.status)}>
                        {order.status}
                      </Badge>
                    </div>
                    <div className="text-sm text-slate-400">
                      {order.shares} shares @ ${order.price} • {order.orderType}
                    </div>
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-white font-semibold">${order.total.toLocaleString()}</div>
                  <div className="text-xs text-slate-500">
                    {new Date(order.timestamp).toLocaleTimeString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
