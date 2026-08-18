import type { StreamEvent } from "../types";

export function EventStream({ events }: { events: StreamEvent[] }) {
  return (
    <div>
      <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
        事件流（SharedContext → SSE）
      </div>
      <div className="event-box">
        {events.length === 0 ? (
          <p className="event-empty">等待 swarm_started …</p>
        ) : (
          events.map((ev, i) => (
            <div className="event-row" key={`${ev.ts}-${ev.name}-${i}`}>
              <span className="ts">{ev.ts}</span>
              <span className="ev">{ev.name}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
