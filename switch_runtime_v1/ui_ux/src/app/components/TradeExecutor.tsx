import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Input } from "./ui/input";
import { Button } from "./ui/button";
import { Label } from "./ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";
import { ArrowUpRight, ArrowDownRight, DollarSign } from "lucide-react";
import { mockStocks } from "../data/mockData";
import { toast } from "sonner";

export function TradeExecutor() {
  const [selectedStock, setSelectedStock] = useState(mockStocks[0]?.symbol || "");
  const [orderType, setOrderType] = useState<"market" | "limit" | "stop">("market");
  const [quantity, setQuantity] = useState("10");
  const [limitPrice, setLimitPrice] = useState("");
  const [stopPrice, setStopPrice] = useState("");

  const stock = mockStocks.find(s => s.symbol === selectedStock);
  const totalValue = stock ? parseFloat(quantity || "0") * stock.price : 0;

  const handleTrade = (type: "buy" | "sell") => {
    if (!selectedStock || !stock) {
      toast.error("No runtime symbol available for order.");
      return;
    }
    const orderDetails = {
      type,
      symbol: selectedStock,
      quantity: parseInt(quantity),
      orderType,
      price: orderType === "market" ? stock?.price : parseFloat(limitPrice || stopPrice),
    };

    toast.success(
      `${type.toUpperCase()} Order Placed`,
      {
        description: `${orderDetails.quantity} shares of ${orderDetails.symbol} at ${
          orderType === "market" ? "market price" : `$${orderDetails.price}`
        }`,
      }
    );

    // Reset form
    setQuantity("10");
    setLimitPrice("");
    setStopPrice("");
  };

  return (
    <Card className="bg-slate-900 border-slate-800">
      <CardHeader>
        <CardTitle className="text-white">Execute Trade</CardTitle>
        <p className="text-sm text-slate-400">Place manual trades</p>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {/* Stock Selection */}
          <div>
            <Label className="text-slate-300">Stock</Label>
            <Select value={selectedStock} onValueChange={setSelectedStock}>
              <SelectTrigger className="bg-slate-800 border-slate-700 text-white mt-1">
                <SelectValue />
              </SelectTrigger>
              <SelectContent className="bg-slate-800 border-slate-700">
                {mockStocks.map((stock) => (
                  <SelectItem key={stock.symbol} value={stock.symbol} className="text-white">
                    {stock.symbol} - ${stock.price.toFixed(2)}
                  </SelectItem>
                ))}
                {mockStocks.length === 0 && (
                  <div className="px-3 py-2 text-sm text-slate-400">No runtime symbols</div>
                )}
              </SelectContent>
            </Select>
          </div>

          {/* Order Type */}
          <div>
            <Label className="text-slate-300">Order Type</Label>
            <Tabs value={orderType} onValueChange={(v) => setOrderType(v as any)} className="mt-1">
              <TabsList className="grid w-full grid-cols-3 bg-slate-800">
                <TabsTrigger value="market" className="data-[state=active]:bg-blue-600">
                  Market
                </TabsTrigger>
                <TabsTrigger value="limit" className="data-[state=active]:bg-blue-600">
                  Limit
                </TabsTrigger>
                <TabsTrigger value="stop" className="data-[state=active]:bg-blue-600">
                  Stop
                </TabsTrigger>
              </TabsList>
            </Tabs>
          </div>

          {/* Quantity */}
          <div>
            <Label className="text-slate-300">Quantity</Label>
            <Input
              type="number"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="bg-slate-800 border-slate-700 text-white mt-1"
              placeholder="Number of shares"
            />
          </div>

          {/* Limit Price */}
          {orderType === "limit" && (
            <div>
              <Label className="text-slate-300">Limit Price</Label>
              <Input
                type="number"
                step="0.01"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                className="bg-slate-800 border-slate-700 text-white mt-1"
                placeholder="Price per share"
              />
            </div>
          )}

          {/* Stop Price */}
          {orderType === "stop" && (
            <div>
              <Label className="text-slate-300">Stop Price</Label>
              <Input
                type="number"
                step="0.01"
                value={stopPrice}
                onChange={(e) => setStopPrice(e.target.value)}
                className="bg-slate-800 border-slate-700 text-white mt-1"
                placeholder="Trigger price"
              />
            </div>
          )}

          {/* Total Value */}
          <div className="p-4 bg-slate-800/50 rounded-lg border border-slate-700">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <DollarSign className="w-4 h-4 text-slate-400" />
                <span className="text-slate-400">Estimated Total</span>
              </div>
              <span className="text-xl text-white font-semibold">
                ${totalValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </span>
            </div>
          </div>

          {/* Action Buttons */}
          <div className="grid grid-cols-2 gap-3">
            <Button
              onClick={() => handleTrade("buy")}
              className="bg-green-600 hover:bg-green-700 text-white"
              disabled={!quantity || parseInt(quantity) <= 0 || !stock}
            >
              <ArrowUpRight className="w-4 h-4 mr-2" />
              Buy
            </Button>
            <Button
              onClick={() => handleTrade("sell")}
              className="bg-red-600 hover:bg-red-700 text-white"
              disabled={!quantity || parseInt(quantity) <= 0 || !stock}
            >
              <ArrowDownRight className="w-4 h-4 mr-2" />
              Sell
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
