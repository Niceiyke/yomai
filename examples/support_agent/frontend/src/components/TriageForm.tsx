import { useState } from "react";
import type { FormEvent } from "react";
import { Zap, Send, Loader2, CheckCircle2, ArrowRight } from "lucide-react";

interface TriageResult {
  ticket_id: string;
  sentiment: string;
  category: string;
  priority: string;
  routing: string;
}

export default function TriageForm({ onTriageComplete }: { onTriageComplete: () => void }) {
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<TriageResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!message.trim() || submitting) return;

    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const r = await fetch("/triage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message.trim() }),
      });
      const data = await r.json();
      setJobId(data.job_id);

      // Poll for completion
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 500));
        const s = await fetch(`/__yomai__/jobs/${data.job_id}`);
        if (!s.ok) continue;
        const job = await s.json();
        if (job.status === "succeeded") {
          setResult(job.result);
          setJobId(null);
          onTriageComplete();
          break;
        } else if (job.status === "failed") {
          setError(job.error || "Triage failed");
          setJobId(null);
          break;
        }
      }
    } catch {
      setError("Connection error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-2xl mx-auto">
        <h2 className="text-lg font-semibold text-zinc-200 flex items-center gap-2 mb-6">
          <Zap size={20} className="text-amber-400" /> Ticket Triage
        </h2>

        <p className="text-sm text-zinc-400 mb-6">
          Submit a customer message for automated triage. The system analyzes sentiment,
          categorizes the issue, and routes it to the appropriate team.
        </p>

        <form onSubmit={submit} className="space-y-4">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Paste a customer message here... e.g. 'My wireless headphones arrived broken and I want a refund immediately!'"
            disabled={submitting}
            rows={4}
            className="w-full bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-violet-500 disabled:opacity-50 resize-none"
          />
          <button
            type="submit"
            disabled={!message.trim() || submitting}
            className="flex items-center gap-2 px-6 py-3 rounded-xl bg-violet-600 text-white hover:bg-violet-500 disabled:opacity-30 transition-colors cursor-pointer font-medium text-sm"
          >
            {submitting ? (
              <><Loader2 size={16} className="animate-spin" /> Processing...</>
            ) : (
              <><Send size={16} /> Submit for Triage</>
            )}
          </button>
        </form>

        {/* Job polling status */}
        {jobId && (
          <div className="mt-6 bg-zinc-800/50 border border-zinc-700/50 rounded-xl p-4 flex items-center gap-3">
            <Loader2 size={18} className="animate-spin text-violet-400" />
            <div>
              <p className="text-sm text-zinc-300">Processing triage...</p>
              <p className="text-xs text-zinc-500 font-mono">{jobId}</p>
            </div>
          </div>
        )}

        {/* Result */}
        {result && (
          <div className="mt-6 bg-green-500/5 border border-green-500/30 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle2 size={18} className="text-green-400" />
              <span className="text-sm font-medium text-green-400">Triage Complete</span>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-zinc-500 w-20">Ticket</span>
                <span className="font-mono text-violet-400">{result.ticket_id}</span>
              </div>
              {[
                { label: "Sentiment", value: result.sentiment },
                { label: "Category", value: result.category },
                { label: "Priority", value: result.priority },
              ].map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <span className="text-zinc-500 w-20">{item.label}</span>
                  <ArrowRight size={12} className="text-zinc-600" />
                  <span className="text-zinc-300">{item.value}</span>
                </div>
              ))}
              <div className="flex items-start gap-2 pt-2 border-t border-zinc-700/50">
                <span className="text-zinc-500 w-20 shrink-0">Routing</span>
                <span className="text-zinc-300 text-xs">{result.routing}</span>
              </div>
            </div>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="mt-6 bg-red-500/5 border border-red-500/30 rounded-xl p-4">
            <p className="text-sm text-red-400">{error}</p>
          </div>
        )}
      </div>
    </div>
  );
}
