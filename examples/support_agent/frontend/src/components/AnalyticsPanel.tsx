import { useState, useEffect } from "react";
import { BarChart3, RefreshCw } from "lucide-react";

interface Stats {
  total: number;
  by_category: Record<string, number>;
  by_priority: Record<string, number>;
  resolved: number;
}

export default function AnalyticsPanel() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const r = await fetch("/analytics");
      const data = await r.json();
      setStats(data.support_analytics);
    } catch {}
    setLoading(false);
  };

  useEffect(() => { fetchStats(); }, []);

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-zinc-200 flex items-center gap-2">
          <BarChart3 size={20} /> Analytics
        </h2>
        <button onClick={fetchStats} className="flex items-center gap-1 text-sm text-zinc-400 hover:text-zinc-200 transition-colors cursor-pointer">
          <RefreshCw size={14} className={loading ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {loading && !stats && (
        <div className="flex items-center justify-center h-40 text-zinc-500">
          <RefreshCw size={24} className="animate-spin" />
        </div>
      )}

      {stats && (
        <div className="space-y-6">
          {/* Summary cards */}
          <div className="grid grid-cols-3 gap-4">
            <StatCard label="Total Tickets" value={stats.total} color="violet" />
            <StatCard label="Resolved" value={stats.resolved} color="green" />
            <StatCard label="Open" value={stats.total - stats.resolved} color="amber" />
          </div>

          {/* By category */}
          <div className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl p-4">
            <h3 className="text-sm font-medium text-zinc-400 mb-3">By Category</h3>
            <div className="space-y-2">
              {Object.entries(stats.by_category).map(([cat, count]) => (
                <div key={cat} className="flex items-center gap-3">
                  <span className="text-sm text-zinc-300 w-24 capitalize">{cat}</span>
                  <div className="flex-1 h-2 bg-zinc-700 rounded-full overflow-hidden">
                    <div className="h-full bg-violet-500 rounded-full transition-all"
                      style={{ width: `${stats.total > 0 ? (count / stats.total) * 100 : 0}%` }} />
                  </div>
                  <span className="text-xs text-zinc-500 w-8 text-right">{count}</span>
                </div>
              ))}
              {Object.keys(stats.by_category).length === 0 && (
                <p className="text-sm text-zinc-600">No data yet</p>
              )}
            </div>
          </div>

          {/* By priority */}
          <div className="bg-zinc-800/50 border border-zinc-700/50 rounded-xl p-4">
            <h3 className="text-sm font-medium text-zinc-400 mb-3">By Priority</h3>
            <div className="flex gap-4">
              {Object.entries(stats.by_priority).map(([pri, count]) => (
                <div key={pri} className="flex items-center gap-2">
                  <span className={`px-2 py-0.5 rounded-full text-xs font-medium border ${
                    pri === "urgent" ? "bg-red-500/10 text-red-400 border-red-500/30"
                    : pri === "high" ? "bg-amber-500/10 text-amber-400 border-amber-500/30"
                    : "bg-blue-500/10 text-blue-400 border-blue-500/30"
                  }`}>
                    {pri}: {count}
                  </span>
                </div>
              ))}
              {Object.keys(stats.by_priority).length === 0 && (
                <p className="text-sm text-zinc-600">No data yet</p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  const colors: Record<string, string> = {
    violet: "border-violet-500/30 bg-violet-500/5",
    green: "border-green-500/30 bg-green-500/5",
    amber: "border-amber-500/30 bg-amber-500/5",
  };
  return (
    <div className={`rounded-xl border px-4 py-3 ${colors[color] || colors.violet}`}>
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-bold text-zinc-200">{value}</p>
    </div>
  );
}
