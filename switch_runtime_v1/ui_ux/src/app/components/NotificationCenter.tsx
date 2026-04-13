import { useState } from "react";
import { Bell, Check, X } from "lucide-react";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "./ui/sheet";
import { mockNotifications } from "../data/newsData";

export function NotificationCenter() {
  const [notifications, setNotifications] = useState(mockNotifications);
  const unreadCount = notifications.filter(n => !n.read).length;

  const markAsRead = (id: string) => {
    setNotifications(notifications.map(n =>
      n.id === id ? { ...n, read: true } : n
    ));
  };

  const markAllAsRead = () => {
    setNotifications(notifications.map(n => ({ ...n, read: true })));
  };

  const removeNotification = (id: string) => {
    setNotifications(notifications.filter(n => n.id !== id));
  };

  const getNotificationIcon = (type: string) => {
    const icons = {
      trade: "💰",
      alert: "⚠️",
      bot: "🤖",
      market: "📈",
    };
    return icons[type as keyof typeof icons] || "📢";
  };

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case "high":
        return "border-l-red-500";
      case "medium":
        return "border-l-yellow-500";
      default:
        return "border-l-blue-500";
    }
  };

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="outline" size="icon" className="relative border-slate-700 text-slate-300 hover:bg-slate-800">
          <Bell className="w-5 h-5" />
          {unreadCount > 0 && (
            <Badge className="absolute -top-1 -right-1 w-5 h-5 flex items-center justify-center p-0 bg-red-600 text-white text-xs">
              {unreadCount}
            </Badge>
          )}
        </Button>
      </SheetTrigger>
      <SheetContent className="bg-slate-900 border-slate-800 w-full sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="text-white">Notifications</SheetTitle>
          <SheetDescription className="text-slate-400">
            Stay updated with your trading activity
          </SheetDescription>
        </SheetHeader>
        <div className="mt-6">
          {unreadCount > 0 && (
            <Button
              variant="outline"
              size="sm"
              onClick={markAllAsRead}
              className="w-full mb-4 border-slate-700 text-slate-300 hover:bg-slate-800"
            >
              <Check className="w-4 h-4 mr-2" />
              Mark all as read
            </Button>
          )}
          <div className="space-y-3 max-h-[calc(100vh-200px)] overflow-y-auto">
            {notifications.length === 0 ? (
              <div className="text-center py-8 text-slate-500">
                No notifications
              </div>
            ) : (
              notifications.map((notification) => (
                <div
                  key={notification.id}
                  className={`p-4 rounded-lg border-l-4 transition-colors ${
                    notification.read ? "bg-slate-800/30" : "bg-slate-800/60"
                  } ${getPriorityColor(notification.priority)}`}
                >
                  <div className="flex items-start gap-3">
                    <div className="text-2xl">{getNotificationIcon(notification.type)}</div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-start justify-between gap-2">
                        <h4 className={`font-medium ${notification.read ? "text-slate-400" : "text-white"}`}>
                          {notification.title}
                        </h4>
                        <div className="flex gap-1">
                          {!notification.read && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => markAsRead(notification.id)}
                              className="h-6 w-6 hover:bg-slate-700"
                            >
                              <Check className="w-4 h-4 text-green-400" />
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => removeNotification(notification.id)}
                            className="h-6 w-6 hover:bg-slate-700"
                          >
                            <X className="w-4 h-4 text-slate-400" />
                          </Button>
                        </div>
                      </div>
                      <p className={`text-sm mt-1 ${notification.read ? "text-slate-500" : "text-slate-300"}`}>
                        {notification.message}
                      </p>
                      <p className="text-xs text-slate-500 mt-2">
                        {new Date(notification.timestamp).toLocaleString()}
                      </p>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
