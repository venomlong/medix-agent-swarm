import type { MemorySession } from "../types";

type Props = {
  sessions: MemorySession[];
  activeId: string;
  open: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onCollapse: () => void;
};

function titleOf(session: MemorySession): string {
  const q = (session.question || "").trim();
  return q || "新对话";
}

export function SessionSidebar({
  sessions,
  activeId,
  open,
  onNewChat,
  onSelect,
  onDelete,
  onCollapse,
}: Props) {
  return (
    <aside className="wb-history" aria-label="会话列表" aria-hidden={!open} {...(!open ? { inert: "" } : {})}>
      <div className="wb-history-head">
        <button type="button" className="wb-new-chat" onClick={onNewChat}>
          新对话
        </button>
        <button type="button" className="wb-history-fold" onClick={onCollapse} aria-label="收起会话列表">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
            <path d="M10.2 3.2 5.4 8l4.8 4.8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
      </div>
      <div className="wb-history-list">
        {sessions.length === 0 ? (
          <p className="wb-history-empty">还没有会话</p>
        ) : (
          sessions.map((s) => (
            <div key={s.id} className={`wb-history-item${s.id === activeId ? " on" : ""}`}>
              <button
                type="button"
                className="wb-history-select"
                title={titleOf(s)}
                onClick={() => onSelect(s.id)}
              >
                <span className="wb-history-title">{titleOf(s)}</span>
                {s.time ? <span className="wb-history-time">{s.time}</span> : null}
              </button>
              <button
                type="button"
                className="session-delete-btn wb-history-delete"
                aria-label={`删除会话 ${titleOf(s)}`}
                title="删除"
                onClick={() => onDelete(s.id)}
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path
                    d="M4 5.2h8M6.2 5.2V3.8h3.6v1.4M5.3 5.2v7.1c0 .5.4.9.9.9h3.6c.5 0 .9-.4.9-.9V5.2"
                    stroke="currentColor"
                    strokeWidth="1.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
