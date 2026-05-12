import { useState, useRef, useEffect, useCallback } from "react";
import type { FormEvent } from "react";
import { Send, Wrench, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

interface Message {
  role: "user" | "assistant" | "tool";
  content: string;
  toolName?: string;
  toolArgs?: Record<string, string>;
  toolResult?: string;
}

export default function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [sessionId] = useState(() => crypto.randomUUID());
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = useCallback(
    async (e?: FormEvent) => {
      e?.preventDefault();
      if (!input.trim() || streaming) return;

      const userMsg: Message = { role: "user", content: input.trim() };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setStreaming(true);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const resp = await fetch("/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
          body: JSON.stringify({ message: userMsg.content }),
          signal: controller.signal,
        });

        const reader = resp.body?.getReader();
        if (!reader) return;

        const decoder = new TextDecoder();
        let buffer = "";
        let assistantContent = "";
        setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === "chunk") {
                assistantContent += data.content;
                setMessages((prev) => {
                  const updated = [...prev];
                  updated[updated.length - 1] = { role: "assistant", content: assistantContent };
                  return updated;
                });
              } else if (data.type === "tool_start") {
                setMessages((prev) => [
                  ...prev,
                  { role: "tool", content: "", toolName: data.name, toolArgs: data.args },
                ]);
              } else if (data.type === "tool_end") {
                setMessages((prev) => {
                  const updated = [...prev];
                  for (let i = updated.length - 1; i >= 0; i--) {
                    if (updated[i].role === "tool" && !updated[i].toolResult) {
                      updated[i] = { ...updated[i], toolResult: data.result };
                      break;
                    }
                  }
                  return updated;
                });
              } else if (data.type === "error") {
                setMessages((prev) => [...prev, { role: "assistant", content: `\u26a0\ufe0f ${data.message}` }]);
              }
            } catch { /* skip */ }
          }
        }
      } catch (err: unknown) {
        if (err instanceof Error && err.name !== "AbortError") {
          setMessages((prev) => [...prev, { role: "assistant", content: "\u26a0\ufe0f Connection lost." }]);
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [input, streaming, sessionId]
  );

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center text-zinc-500 max-w-md">
              <Wrench size={48} className="mx-auto mb-4 text-zinc-600" />
              <h2 className="text-xl font-semibold text-zinc-300 mb-2">Acme Support Agent</h2>
              <p className="text-sm">Look up orders, check inventory, process refunds, escalate issues.</p>
              <div className="mt-4 flex flex-wrap gap-2 justify-center">
                {["Check order ORD-1001", "What's in stock?", "I need a refund"].map((q) => (
                  <button key={q} onClick={() => { setInput(q); inputRef.current?.focus(); }}
                    className="px-3 py-1.5 text-xs rounded-full border border-zinc-700 text-zinc-400 hover:border-violet-500 hover:text-violet-300 transition-colors cursor-pointer">
                    {q}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
            <div className={`max-w-[75%] rounded-2xl px-4 py-3 ${
              msg.role === "user" ? "bg-violet-600 text-white"
              : msg.role === "tool" ? "bg-zinc-800/50 border border-zinc-700/50"
              : "bg-zinc-800/80 text-zinc-200"
            }`}>
              {msg.role === "tool" ? (
                <div className="text-sm">
                  <div className="flex items-center gap-2 text-violet-400 mb-1">
                    <Wrench size={14} />
                    <span className="font-medium">{msg.toolName}</span>
                    {msg.toolResult ? <CheckCircle2 size={14} className="text-green-400 ml-auto" />
                      : <Loader2 size={14} className="animate-spin ml-auto" />}
                  </div>
                  {msg.toolArgs && (
                    <div className="text-zinc-500 text-xs mb-1">
                      {Object.entries(msg.toolArgs).map(([k, v]) => (
                        <span key={k} className="mr-2"><span className="text-zinc-400">{k}</span>={JSON.stringify(v)}</span>
                      ))}
                    </div>
                  )}
                  {msg.toolResult && (
                    <div className="text-zinc-400 text-xs mt-1 border-t border-zinc-700/50 pt-1 truncate">{msg.toolResult}</div>
                  )}
                </div>
              ) : (
                <p className={`text-sm leading-relaxed whitespace-pre-wrap ${
                  streaming && i === messages.length - 1 && msg.role === "assistant" ? "streaming-cursor" : ""
                }`}>{msg.content}</p>
              )}
            </div>
          </div>
        ))}

        {streaming && messages[messages.length - 1]?.role !== "assistant" && (
          <div className="flex justify-start"><div className="bg-zinc-800/80 rounded-2xl px-4 py-3">
            <Loader2 size={16} className="animate-spin text-zinc-500" /></div></div>
        )}
        <div ref={bottomRef} />
      </div>

      <form onSubmit={sendMessage} className="p-4 border-t border-zinc-800">
        <div className="max-w-3xl mx-auto flex gap-3">
          <input ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about orders, inventory, refunds..." disabled={streaming}
            className="flex-1 bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-sm text-zinc-200 placeholder-zinc-500 focus:outline-none focus:border-violet-500 disabled:opacity-50" />
          {streaming ? (
            <button type="button" onClick={() => { abortRef.current?.abort(); setStreaming(false); }}
              className="px-4 py-3 rounded-xl bg-red-600/20 border border-red-500/30 text-red-400 hover:bg-red-600/30 transition-colors cursor-pointer">
              <AlertCircle size={18} />
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()}
              className="px-4 py-3 rounded-xl bg-violet-600 text-white hover:bg-violet-500 disabled:opacity-30 transition-colors cursor-pointer">
              <Send size={18} />
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
