"use client";

import { useEffect, useRef, useState } from "react";
import { getCopilotThread, sendCopilotMessage } from "@/lib/api";
import type { CopilotMessage } from "@/lib/types";

export default function CopilotPage() {
  const [messages, setMessages] = useState<CopilotMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [conversationId, setConversationId] = useState<string | undefined>();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (conversationId) getCopilotThread(conversationId).then(setMessages);
  }, [conversationId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  async function handleSend() {
    if (!input.trim() || sending) return;
    setError("");
    const userMsg: CopilotMessage = {
      id: `local_${Date.now()}`,
      role: "user",
      content: input.trim(),
      createdAt: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setSending(true);
    try {
      const reply = await sendCopilotMessage(userMsg.content, conversationId);
      setConversationId(reply.conversationId);
      setMessages((prev) => [...prev, reply]);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSending(false);
    }
  }

  const lastAssistant = [...messages].reverse().find((message) => message.role === "assistant");

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)]">
      <header className="mb-6">
        <span className="font-mono text-xs tracking-[0.3em] text-mint uppercase">Engine 4</span>
        <h1 className="font-display text-3xl font-semibold mt-1">Career Copilot</h1>
        <p className="text-muted mt-1">Retrieval over your conversation history and candidate context — routed through the orchestrator, not a raw model call.</p>
      </header>

      <div className="grid lg:grid-cols-[1fr,320px] gap-5 min-h-0 flex-1">
      <div className="bg-panel panel-border rounded-xl p-6 overflow-y-auto flex flex-col gap-4">
        {messages.map((m) => (
          <div key={m.id} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[70%] whitespace-pre-line rounded-lg px-4 py-3 text-sm leading-relaxed ${
                m.role === "user" ? "bg-signal text-ink" : "bg-panel-raised text-ivory"
              }`}
            >
              {m.content}
              {m.role === "assistant" && Boolean(m.suggestedActions?.length) && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {m.suggestedActions?.map((action) => (
                    <span key={action} className="rounded-full bg-panel px-2 py-1 text-[10px] font-mono uppercase tracking-wider text-mint">
                      {action}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="bg-panel-raised rounded-lg px-4 py-3 text-sm text-muted font-mono">thinking…</div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <aside className="bg-panel panel-border rounded-xl p-5 overflow-auto">
        <h2 className="font-display font-semibold text-lg">Copilot engine output</h2>
        <div className="mt-4">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Conversation memory</p>
          <p className="text-sm mt-1">{conversationId ? "Active RAG thread" : "New thread"}</p>
        </div>
        <div className="mt-5">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted">Actionable guidance</p>
          <div className="mt-2 flex flex-col gap-2">
            {(lastAssistant?.suggestedActions?.length ? lastAssistant.suggestedActions : ["Ask a question to generate actions"]).map((action) => (
              <span key={action} className="rounded-lg bg-panel-raised px-3 py-2 text-xs text-muted">
                {action}
              </span>
            ))}
          </div>
        </div>
        <div className="mt-5">
          <p className="font-mono text-[10px] uppercase tracking-widest text-muted">JSON output</p>
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-panel-raised p-4 text-xs leading-relaxed text-muted">
            {JSON.stringify(
              {
                conversation_id: conversationId ?? null,
                reply: lastAssistant?.content ?? null,
                suggested_actions: lastAssistant?.suggestedActions ?? [],
              },
              null,
              2
            )}
          </pre>
        </div>
      </aside>
      </div>

      {error && (
        <div className="mt-4 rounded-lg border border-danger/60 bg-danger/10 p-4 text-sm text-danger">
          {error}
        </div>
      )}

      <div className="flex gap-3 mt-4">
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSend()}
          placeholder="Ask about your resume, a match, or interview prep…"
          className="flex-1 bg-panel panel-border rounded-md px-4 py-3 outline-none focus:border-signal text-sm"
        />
        <button
          onClick={handleSend}
          disabled={sending}
          className="bg-signal text-ink font-semibold px-6 rounded-md hover:brightness-110 transition disabled:opacity-60"
        >
          Send
        </button>
      </div>
    </div>
  );
}
