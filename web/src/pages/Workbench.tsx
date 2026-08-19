import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AnswerCard } from "../components/AnswerCard";
import { Composer } from "../components/Composer";
import { EventStream } from "../components/EventStream";
import { LeafLogo } from "../components/LeafLogo";
import { SessionSidebar } from "../components/SessionSidebar";
import { Timeline } from "../components/Timeline";
import { TimeoutFallback } from "../components/TimeoutFallback";
import {
  createSessionId,
  deleteSession,
  getSessionMessages,
  getSessions,
  loadStoredSessionId,
  mapHistoryToChatMessages,
  sendChat,
  storeSessionId,
  USE_MOCK,
} from "../api/client";
import { DEFAULT_QUESTION, FOLLOWUP_HINT, MEMORY_SESSIONS } from "../mock/data";
import { simulateConsultation } from "../mock/simulate";
import type {
  AnswerPayload,
  AnswerReveal,
  ChatMessage,
  MemorySession,
  RoutingMode,
  StreamEvent,
  TimelineStep,
} from "../types";

function timeLabel(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const EXAMPLE_PROMPTS = [
  { title: "急症短路", text: "胸口压榨性疼痛还出冷汗" },
  { title: "知识库引用", text: "高血压饮食注意什么" },
  { title: "普通咨询", text: DEFAULT_QUESTION },
] as const;

const EMPTY_REVEAL: AnswerReveal = {
  alert: false,
  suggestions: false,
  sources: false,
  disclaimer: false,
};

export function Workbench() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSession = (searchParams.get("session") || "").trim();
  const [input, setInput] = useState(DEFAULT_QUESTION);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [routing, setRouting] = useState<RoutingMode>("idle");
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const [historyReady, setHistoryReady] = useState(USE_MOCK);
  const [recentSessions, setRecentSessions] = useState<MemorySession[]>(
    USE_MOCK ? MEMORY_SESSIONS : []
  );
  const [historyOpen, setHistoryOpen] = useState(
    () => typeof window === "undefined" || !window.matchMedia("(max-width: 1100px)").matches
  );
  const listRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const sessionIdRef = useRef<string>(requestedSession || loadStoredSessionId());
  const runningRef = useRef(false);
  const historySeqRef = useRef(0);

  useEffect(() => {
    return () => cancelRef.current?.();
  }, []);

  useEffect(() => {
    runningRef.current = running;
  }, [running]);

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1100px)");
    const apply = () => setHistoryOpen(!mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);

  useEffect(() => {
    if (USE_MOCK) return;
    let cancelled = false;
    getSessions()
      .then((data) => {
        if (!cancelled) setRecentSessions(data.sessions ?? []);
      })
      .catch(() => {
        if (!cancelled) setRecentSessions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const sid = requestedSession || loadStoredSessionId();
    sessionIdRef.current = sid;
    if (sid) storeSessionId(sid);

    if (USE_MOCK) {
      setHistoryReady(true);
      return;
    }
    if (!sid) {
      setMessages([]);
      setHistoryReady(true);
      return;
    }

    let cancelled = false;
    const seq = ++historySeqRef.current;
    setHistoryReady(false);
    setRouting("idle");
    setSteps([]);
    setEvents([]);
    setTimedOut(false);
    getSessionMessages(sid)
      .then((data) => {
        if (cancelled || seq !== historySeqRef.current || runningRef.current) return;
        setMessages(mapHistoryToChatMessages(data.messages ?? []));
        setHistoryReady(true);
      })
      .catch(() => {
        if (cancelled || seq !== historySeqRef.current || runningRef.current) return;
        setMessages([]);
        setHistoryReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, [requestedSession]);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, routing, steps, events, timedOut]);

  const placeholder = messages.length > 0 ? FOLLOWUP_HINT : "输入健康咨询问题，或发送预填示例开始演示";

  const sideStatus = useMemo(() => {
    if (timedOut) return { text: "超时", cls: "pill clay" };
    if (running) return { text: "进行中", cls: "pill" };
    if (steps.some((s) => s.status === "done")) return { text: "已完成", cls: "pill" };
    return { text: "待开始", cls: "pill ghost" };
  }, [running, steps, timedOut]);

  function send(text?: string) {
    const question = (text ?? input).trim();
    if (!question || running) return;

    historySeqRef.current += 1;
    cancelRef.current?.();
    setTimedOut(false);
    setRunning(true);
    setInput("");
    setEvents([]);
    setSteps([]);
    setRouting("pending");

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: "user",
      text: question,
      timeLabel: timeLabel(),
    };
    const assistantId = `a-${Date.now()}`;

    setMessages((prev) => [...prev, userMsg]);

    if (!sessionIdRef.current) {
      sessionIdRef.current = createSessionId();
      storeSessionId(sessionIdRef.current);
    }

    const handlers = {
      onRouting: (mode: Exclude<RoutingMode, "idle">, subtaskCount?: number) => {
        setRouting(mode);
        if (mode === "swarm" || mode === "single" || mode === "emergency") {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? m
                : m.id === userMsg.id
                  ? { ...m, routing: mode, subtaskCount }
                  : m
            )
          );
        }
      },
      onSteps: setSteps,
      onEvent: (ev: StreamEvent) => {
        setEvents((prev) => [...prev, ev]);
        if (ev.name === "timeout_occurred" || ev.name.startsWith("timeout")) {
          setTimedOut(true);
        }
      },
      onSession: (sid: string) => {
        if (sid) {
          sessionIdRef.current = sid;
          storeSessionId(sid);
        }
      },
      onAnswerStart: (draft: AnswerPayload) => {
        setMessages((prev) => [
          ...prev,
          {
            id: assistantId,
            role: "assistant",
            text: "",
            streaming: true,
            reveal: { ...EMPTY_REVEAL },
            answer: draft,
          },
        ]);
      },
      onAnswerDelta: (text: string) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId && m.answer
              ? { ...m, text, answer: { ...m.answer, body: text } }
              : m
          )
        );
      },
      onReveal: (key: "alert" | "suggestions" | "sources" | "disclaimer") => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, reveal: { ...(m.reveal ?? EMPTY_REVEAL), [key]: true } }
              : m
          )
        );
      },
      onAnswerDone: (payload: AnswerPayload) => {
        if (payload.timedOut) setTimedOut(true);
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  streaming: false,
                  text: payload.body,
                  answer: payload,
                  reveal: {
                    alert: true,
                    suggestions: true,
                    sources: true,
                    disclaimer: true,
                  },
                }
              : m
          )
        );
      },
      onDone: () => {
        setRunning(false);
        setRouting((r) => (r === "pending" ? "idle" : r));
        if (!USE_MOCK) {
          getSessions()
            .then((data) => setRecentSessions(data.sessions ?? []))
            .catch(() => {});
        }
      },
    };

    cancelRef.current = USE_MOCK
      ? simulateConsultation(question, handlers)
      : sendChat(question, sessionIdRef.current, handlers);
  }

  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const activeSessionId = requestedSession || sessionIdRef.current;
  const firstUserText = messages.find((m) => m.role === "user")?.text?.trim() || "";

  const sidebarSessions = useMemo(() => {
    const list = [...recentSessions];
    const sid = activeSessionId;
    if (!sid) return list;
    const idx = list.findIndex((s) => s.id === sid);
    if (idx < 0) {
      list.unshift({
        id: sid,
        time: firstUserText ? "刚刚" : "",
        question: firstUserText || "新对话",
        mode: "Swarm",
        elapsed: "",
        summary: "",
      });
      return list;
    }
    if (firstUserText && !list[idx].question) {
      list[idx] = { ...list[idx], question: firstUserText };
    }
    return list;
  }, [recentSessions, activeSessionId, firstUserText]);

  function collapseHistoryIfNarrow() {
    if (window.matchMedia("(max-width: 1100px)").matches) {
      setHistoryOpen(false);
    }
  }

  function openSession(id: string) {
    if (!id || id === activeSessionId) {
      collapseHistoryIfNarrow();
      return;
    }
    cancelRef.current?.();
    runningRef.current = false;
    setRunning(false);
    setSearchParams({ session: id });
    collapseHistoryIfNarrow();
  }

  function startNewChat() {
    cancelRef.current?.();
    runningRef.current = false;
    historySeqRef.current += 1;
    setRunning(false);
    setMessages([]);
    setRouting("idle");
    setSteps([]);
    setEvents([]);
    setTimedOut(false);
    setHistoryReady(true);
    const sid = createSessionId();
    sessionIdRef.current = sid;
    storeSessionId(sid);
    setSearchParams({ session: sid });
    collapseHistoryIfNarrow();
  }

  function newChat() {
    const isBlankDraft =
      !!activeSessionId &&
      messages.length === 0 &&
      historyReady &&
      !recentSessions.some((s) => s.id === activeSessionId);
    if (isBlankDraft) {
      collapseHistoryIfNarrow();
      return;
    }
    startNewChat();
  }

  async function removeSession(id: string) {
    if (!id) return;
    if (!window.confirm("确定删除这条会话？")) return;
    if (!USE_MOCK) {
      try {
        await deleteSession(id);
      } catch (err: unknown) {
        window.alert(err instanceof Error ? err.message : "删除失败，请稍后重试。");
        return;
      }
    }
    setRecentSessions((prev) => prev.filter((s) => s.id !== id));
    if (id === activeSessionId) {
      startNewChat();
    }
  }

  return (
    <div className={`page-workbench${historyOpen ? " history-open" : " history-collapsed"}`}>
      {historyOpen ? (
        <button
          type="button"
          className="wb-history-backdrop"
          aria-label="关闭会话列表"
          onClick={() => setHistoryOpen(false)}
        />
      ) : null}
      <SessionSidebar
        sessions={sidebarSessions}
        activeId={activeSessionId}
        open={historyOpen}
        onNewChat={newChat}
        onSelect={openSession}
        onDelete={(id) => {
          void removeSession(id);
        }}
        onCollapse={() => setHistoryOpen(false)}
      />
      <section className="wb-chat">
        {historyOpen ? null : (
          <div className="wb-chat-tools">
            <button type="button" className="pill ghost" onClick={() => setHistoryOpen(true)}>
              会话
            </button>
          </div>
        )}
        <div className="wb-messages" ref={listRef}>
          {messages.length === 0 && historyReady ? (
            <>
              <div className="time-center">今天</div>
              <div className="example-grid">
                {EXAMPLE_PROMPTS.map((ex) => (
                  <button
                    key={ex.title}
                    type="button"
                    className="card example-card"
                    onClick={() => {
                      setInput(ex.text);
                    }}
                  >
                    <h3>{ex.title}</h3>
                    <p>{ex.text}</p>
                  </button>
                ))}
              </div>
              <p className="muted" style={{ margin: "4px 4px 0", fontSize: 12 }}>
                点击卡片预填输入框，再发送。
                {USE_MOCK
                  ? " 顶栏若写着「示意 Mock」，看到的是本地假数据，不是真实急症/用量。"
                  : " 顶栏应为「真实 SSE」；请先启动后端 :8000。"}
              </p>
            </>
          ) : null}

          {messages.map((m, idx) => (
            <div key={m.id} style={{ display: "contents" }}>
              {m.role === "user" ? (
                <>
                  {idx === 0 || messages[idx - 1]?.timeLabel !== m.timeLabel ? (
                    <div className="time-center">{m.timeLabel}</div>
                  ) : null}
                  <div className="bubble-user">{m.text}</div>
                  {lastUser?.id === m.id && routing === "pending" ? (
                    <div className="pill routing-pill pending">
                      <LeafLogo size={11} fill="#5F7F68" />
                      智能路由中……
                    </div>
                  ) : null}
                  {m.routing === "swarm" ? (
                    <div className="pill routing-pill">
                      <LeafLogo size={11} fill="#5F7F68" />
                      智能路由 · 群体协作模式 · 已分解 {m.subtaskCount ?? 3} 个子任务
                    </div>
                  ) : null}
                  {m.routing === "single" ? (
                    <div className="pill routing-pill">
                      <LeafLogo size={11} fill="#5F7F68" />
                      智能路由 · 单 Agent 快速应答
                    </div>
                  ) : null}
                  {m.routing === "emergency" ? (
                    <div className="pill routing-pill emergency">
                      <LeafLogo size={11} fill="#B85C4A" />
                      急症短路 · 已跳过常规 Swarm
                    </div>
                  ) : null}
                </>
              ) : m.answer ? (
                <>
                  <AnswerCard answer={m.answer} streaming={m.streaming} reveal={m.reveal} />
                  {timedOut ? <TimeoutFallback agentCount={m.answer.agentCount} /> : null}
                </>
              ) : null}
            </div>
          ))}
        </div>
        <div className="wb-composer-wrap">
          <Composer
            value={input}
            placeholder={placeholder}
            disabled={running}
            onChange={setInput}
            onSubmit={() => send()}
          />
        </div>
      </section>

      <aside className="wb-side">
        <div className="side-head">
          <strong>协作过程</strong>
          <span style={{ flex: 1 }} />
          <span className={sideStatus.cls} style={{ fontSize: 11 }}>
            {sideStatus.text}
          </span>
        </div>
        <div className="wb-side-body">
          <Timeline steps={steps} />
          <EventStream events={events} />
        </div>
      </aside>
    </div>
  );
}
