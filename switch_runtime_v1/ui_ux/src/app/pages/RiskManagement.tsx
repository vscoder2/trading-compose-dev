import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Slider } from "../components/ui/slider";
import { Switch } from "../components/ui/switch";
import { Label } from "../components/ui/label";
import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import { Shield, AlertTriangle, TrendingDown, Lock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

export function RiskManagement() {
  const [maxPositionSize, setMaxPositionSize] = useState([25]);
  const [stopLossPercent, setStopLossPercent] = useState([5]);
  const [maxDailyLoss, setMaxDailyLoss] = useState([2]);
  const [maxDrawdown, setMaxDrawdown] = useState([15]);
  const [autoStopLoss, setAutoStopLoss] = useState(true);
  const [takeProfitEnabled, setTakeProfitEnabled] = useState(true);
  const [trailingStop, setTrailingStop] = useState(false);
  const [positionSizeLimit, setPositionSizeLimit] = useState(true);

  const saveSettings = () => {
    toast.success("Risk settings saved successfully");
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl text-white font-semibold">Risk Management</h2>
        <p className="text-slate-400 mt-1">Configure your risk parameters and protection rules</p>
      </div>

      {/* Risk Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="bg-gradient-to-br from-green-900/20 to-green-800/10 border-green-800/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-green-400">Risk Level</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">Conservative</div>
            <p className="text-sm text-green-400 mt-1">Protection enabled</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Current Exposure</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">64%</div>
            <p className="text-sm text-slate-400 mt-1">of portfolio</p>
          </CardContent>
        </Card>

        <Card className="bg-slate-900 border-slate-800">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm text-slate-400">Protected Capital</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl text-white">$124,890</div>
            <p className="text-sm text-green-400 mt-1">95.6% secured</p>
          </CardContent>
        </Card>
      </div>

      {/* Position Limits */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-blue-400" />
            <CardTitle className="text-white">Position Limits</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Control maximum position sizes</p>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <div className="flex items-center justify-between mb-3">
              <Label className="text-slate-300">Maximum Position Size</Label>
              <span className="text-white font-semibold">{maxPositionSize[0]}%</span>
            </div>
            <Slider
              value={maxPositionSize}
              onValueChange={setMaxPositionSize}
              max={50}
              step={1}
              className="mb-2"
            />
            <p className="text-sm text-slate-500">
              Maximum percentage of portfolio in a single position
            </p>
          </div>

          <div className="flex items-center justify-between p-4 bg-slate-800/50 rounded-lg">
            <div>
              <Label className="text-slate-300">Enable Position Size Limits</Label>
              <p className="text-sm text-slate-500 mt-1">Prevent over-concentration</p>
            </div>
            <Switch
              checked={positionSizeLimit}
              onCheckedChange={setPositionSizeLimit}
            />
          </div>
        </CardContent>
      </Card>

      {/* Stop Loss Configuration */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <TrendingDown className="w-5 h-5 text-red-400" />
            <CardTitle className="text-white">Stop Loss Protection</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Automatic loss prevention</p>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center justify-between p-4 bg-slate-800/50 rounded-lg">
            <div>
              <Label className="text-slate-300">Enable Auto Stop Loss</Label>
              <p className="text-sm text-slate-500 mt-1">Automatically exit losing positions</p>
            </div>
            <Switch
              checked={autoStopLoss}
              onCheckedChange={setAutoStopLoss}
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-3">
              <Label className="text-slate-300">Stop Loss Percentage</Label>
              <span className="text-white font-semibold">{stopLossPercent[0]}%</span>
            </div>
            <Slider
              value={stopLossPercent}
              onValueChange={setStopLossPercent}
              max={20}
              step={0.5}
              className="mb-2"
              disabled={!autoStopLoss}
            />
            <p className="text-sm text-slate-500">
              Exit position when loss reaches this percentage
            </p>
          </div>

          <div className="flex items-center justify-between p-4 bg-slate-800/50 rounded-lg">
            <div>
              <Label className="text-slate-300">Trailing Stop Loss</Label>
              <p className="text-sm text-slate-500 mt-1">Lock in profits as price rises</p>
            </div>
            <Switch
              checked={trailingStop}
              onCheckedChange={setTrailingStop}
              disabled={!autoStopLoss}
            />
          </div>
        </CardContent>
      </Card>

      {/* Daily Loss Limits */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-yellow-400" />
            <CardTitle className="text-white">Daily Limits</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Prevent excessive losses in a single day</p>
        </CardHeader>
        <CardContent className="space-y-6">
          <div>
            <div className="flex items-center justify-between mb-3">
              <Label className="text-slate-300">Maximum Daily Loss</Label>
              <span className="text-white font-semibold">{maxDailyLoss[0]}%</span>
            </div>
            <Slider
              value={maxDailyLoss}
              onValueChange={setMaxDailyLoss}
              max={10}
              step={0.5}
              className="mb-2"
            />
            <p className="text-sm text-slate-500">
              Halt all trading when daily loss reaches this limit
            </p>
          </div>

          <div>
            <div className="flex items-center justify-between mb-3">
              <Label className="text-slate-300">Maximum Drawdown</Label>
              <span className="text-white font-semibold">{maxDrawdown[0]}%</span>
            </div>
            <Slider
              value={maxDrawdown}
              onValueChange={setMaxDrawdown}
              max={30}
              step={1}
              className="mb-2"
            />
            <p className="text-sm text-slate-500">
              Maximum decline from peak portfolio value
            </p>
          </div>
        </CardContent>
      </Card>

      {/* Take Profit Settings */}
      <Card className="bg-slate-900 border-slate-800">
        <CardHeader>
          <div className="flex items-center gap-2">
            <Lock className="w-5 h-5 text-green-400" />
            <CardTitle className="text-white">Take Profit Rules</CardTitle>
          </div>
          <p className="text-sm text-slate-400">Secure gains automatically</p>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center justify-between p-4 bg-slate-800/50 rounded-lg">
            <div>
              <Label className="text-slate-300">Enable Auto Take Profit</Label>
              <p className="text-sm text-slate-500 mt-1">Lock in profits at target levels</p>
            </div>
            <Switch
              checked={takeProfitEnabled}
              onCheckedChange={setTakeProfitEnabled}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <Label className="text-slate-300">Target Profit %</Label>
              <Input
                type="number"
                defaultValue="10"
                className="bg-slate-800 border-slate-700 text-white mt-2"
                disabled={!takeProfitEnabled}
              />
            </div>
            <div>
              <Label className="text-slate-300">Partial Exit %</Label>
              <Input
                type="number"
                defaultValue="50"
                className="bg-slate-800 border-slate-700 text-white mt-2"
                disabled={!takeProfitEnabled}
              />
            </div>
          </div>

          <p className="text-sm text-slate-500">
            Exit {takeProfitEnabled ? "50" : "0"}% of position when profit reaches target
          </p>
        </CardContent>
      </Card>

      {/* Current Rules Summary */}
      <Card className="bg-gradient-to-br from-blue-900/20 to-purple-900/20 border-blue-800/50">
        <CardHeader>
          <CardTitle className="text-white">Active Protection Rules</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-3">
            {autoStopLoss && (
              <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg">
                <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                <span className="text-slate-300">
                  Auto stop-loss at {stopLossPercent[0]}% loss
                </span>
              </div>
            )}
            {takeProfitEnabled && (
              <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg">
                <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                <span className="text-slate-300">
                  Take profit enabled at target levels
                </span>
              </div>
            )}
            {positionSizeLimit && (
              <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg">
                <div className="w-2 h-2 bg-green-500 rounded-full"></div>
                <span className="text-slate-300">
                  Maximum position size: {maxPositionSize[0]}%
                </span>
              </div>
            )}
            <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span className="text-slate-300">
                Daily loss limit: {maxDailyLoss[0]}%
              </span>
            </div>
            <div className="flex items-center gap-3 p-3 bg-slate-900/50 rounded-lg">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <span className="text-slate-300">
                Maximum drawdown: {maxDrawdown[0]}%
              </span>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Save Button */}
      <div className="flex justify-end">
        <Button onClick={saveSettings} className="bg-blue-600 hover:bg-blue-700">
          Save Risk Settings
        </Button>
      </div>
    </div>
  );
}
