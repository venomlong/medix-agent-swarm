import { useEffect, useMemo, useRef, useState } from "react";
import { AnswerCard } from "../components/AnswerCard";
import { Composer } from "../components/Composer";
import { EventStream } from "../components/EventStream";
import { LeafLogo } from "../components/LeafLogo";
import { Timeline } from "../components/Timeline";
import { TimeoutFallback } from "../components/TimeoutFallback";
import { createSessionId, loadStoredSessionId, sendChat, storeSessionId, USE_MOCK } from "../api/client";
import { DEFAULT_QUESTION, FOLLOWUP_HINT } from "../mock/data";
import { simulateConsultation } from "../mock/simulate";
import type {
  AnswerPayload,
  AnswerReveal,
  ChatMessage,
  RoutingMode,
  StreamEvent,
  TimelineStep,
} from "../types";

function timeLabel(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `今天 ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const EMPTY_REVEAL: AnswerReveal = {
  alert: false,
  suggestions: false,
  sources: false,
  disclaimer: false,
};

export function Workbench() {
  const [input, setInput] = useState(DEFAULT_QUESTION);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [routing, setRouting] = useState<RoutingMode>("idle");
  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [timedOut, setTimedOut] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);
  const sessionIdRef = useRef<string>(loadStoredSessionId());

  useEffect(() => {
    return () => cancelRef.current?.();
  }, []);

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
        if (mode === "swarm" || mode === "single") {
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
      },
    };

    cancelRef.current = USE_MOCK
      ? simulateConsultation(question, handlers)
      : sendChat(question, sessionIdRef.current, handlers);
  }

  const lastUser = [...messages].reverse().find((m) => m.role === "user");

  return (
    <div className="page-workbench">
      <section className="wb-chat">
        <div className="wb-messages" ref={listRef}>
          {messages.length === 0 ? (
            <>
              <div className="time-center">今天</div>
              <button
                type="button"
                className="card example-card"
                onClick={() => {
                  setInput(DEFAULT_QUESTION);
                }}
              >
                <h3>示例问题</h3>
                <p>{DEFAULT_QUESTION}</p>
                <p className="muted" style={{ marginTop: 8, fontSize: 12 }}>
                  已预填到输入框。点击发送，将走智能路由 → 协作时间线 → 答案流出
                  {USE_MOCK ? "（当前为本地 mock）。" : "。请先启动后端 :8000。"}
                </p>
              </button>
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
        <Timeline steps={steps} />
        <EventStream events={events} />
      </aside>
    </div>
  );
}
