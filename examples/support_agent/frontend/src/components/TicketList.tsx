import { useState, useEffect } from "react";
import { Ticket, Clock, CheckCircle2, RefreshCw } from "lucide-react";

interface TicketData {
  ticket_id: string;
  session_id: string;
  sentiment: string;
  category: string;
  priority: string;
  routed_to: string;
  resolved: boolean;
}

const PRIORITY_COLORS: Record<string, string> = {
  urgent: "bg-red-500/10 text-red-400 border-red-500/30",
  high: "bg-amber-500/10 text-amber-400 border-amber-500/30",
  medium: "bg-blue-500/10 text-blue-400 border-blue-500/30",
  low: "bg-zinc-500/10 text-zinc-400 border-zinc-500/30",
};

const SENTIMENT_COLORS: Record<string, string> = {
  urgent: "text-red-400",
  negative: "text-amber-400",
  neutral: "text-zinc-400",
  positive: "text-green-400",
};

export default function TicketList() {
  const [tickets, setTickets] = useState<TicketData[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchTickets = async () => {
    setLoading(true);
    try {
      // Get analytics to find ticket IDs, then fetch each
      const aResp = await fetch("/analytics");
      const analytics = await aResp.json();
      const total = analytics.support_analytics?.total || 0;
      if (total === 0) { setTickets([]); setLoading(false); return; }

      const found: TicketData[] = [];
      for (let i = 1; i <= total + 5; i++) {
        try {
          const r = await fetch(`/tickets/TKT-${1000 + i}`);
          if (r.ok) found.push(await r.json());
        } catch { break; }
      }
      setTickets(found);
    } catch { /* backend may be down */ }
    setLoading(false);
  };

  useEffect(() => { fetchTickets(); }, []);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-zinc-200 flex items-center gap-2">
          <Ticket size={20} /> Support Tickets
        </h2>
        <button onClick={fetchTickets} className="flex items-center gap-1 text-sm text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {loading && tickets.length === 0 && (
        <div className="flex items-center justify-center h-40 text-zinc-500">
          <RefreshCw size={24} className="animate-spin" />
        </div>
      )}

      {!loading && tickets.length === 0 && (
        <div className="flex flex-col items-center justify-center h-40 text-zinc-500 gap-2">
          <Ticket size={32} className="text-zinc-700" />
          <p className="text-sm">No tickets yet. Submit a triage to create one.</p>
        </div>
      )}

      <div className="space-y-3">
        {tickets.map((t) => (
          <div key={t.ticket_id} className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl p-4 hover:border-zinc-600/50 transition-colors">
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-sm text-violet-400">{t.ticket_id}</span>
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${PRIORITY_COLORS[t.priority] || PRIORITY_COLORS.medium}`}>
                {t.priority}
              </span>
            </div>
            <div className="flex items-center gap-3 text-xs text-zinc-400">
              <span className={SENTIMENT_COLORS[t.sentiment] || ""}>{t.sentiment}</span>
              <span>|</span>
              <span>{t.category}</span>
              <span>|</span>
              <span className="flex items-center gap-1">
                {t.resolved ? <CheckCircle2 size={12} className="text-green-400" /> : <Clock size={12} />}
                {t.resolved ? "resolved" : "open"}
              </span>
            </div>
            {t.routed_to && (
              <p className="text-xs text-zinc-500 mt-2 truncate">{t.routed_to}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
