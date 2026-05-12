import { useState, useCallback } from "react";
import { MessageSquare, Ticket, BarChart3, Zap } from "lucide-react";
import ChatPanel from "./components/ChatPanel";
import TicketList from "./components/TicketList";
import AnalyticsPanel from "./components/AnalyticsPanel";
import TriageForm from "./components/TriageForm";

type Tab = "chat" | "tickets" | "analytics" | "triage";

export default function App() {
  const [tab, setTab] = useState<Tab>("chat");
  const [refreshKey, setRefreshKey] = useState(0);

  const triggerRefresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  const tabs: { id: Tab; label: string; icon: React.ReactNode }[] = [
    { id: "chat", label: "Chat", icon: <MessageSquare size={18} /> },
    { id: "triage", label: "Triage", icon: <Zap size={18} /> },
    { id: "tickets", label: "Tickets", icon: <Ticket size={18} /> },
    { id: "analytics", label: "Analytics", icon: <BarChart3 size={18} /> },
  ];

  return (
    <div className="h-screen flex flex-col bg-[#0f1117]">
      {/* Header */}
      <header className="h-14 border-b border-zinc-800 flex items-center px-6 gap-6 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 flex items-center justify-center text-white font-bold text-sm">
            A
          </div>
          <span className="font-semibold text-zinc-100">Acme Support</span>
        </div>
        <nav className="flex gap-1 ml-8">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors cursor-pointer ${
                tab === t.id
                  ? "bg-violet-500/20 text-violet-300"
                  : "text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/50"
              }`}
            >
              {t.icon}
              {t.label}
            </button>
          ))}
        </nav>
      </header>

      {/* Content */}
      <main className="flex-1 overflow-hidden">
        {tab === "chat" && <ChatPanel />}
        {tab === "triage" && <TriageForm onTriageComplete={triggerRefresh} />}
        {tab === "tickets" && <TicketList key={refreshKey} />}
        {tab === "analytics" && <AnalyticsPanel key={refreshKey} />}
      </main>
    </div>
  );
}
