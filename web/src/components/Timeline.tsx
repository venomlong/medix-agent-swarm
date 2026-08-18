import type { StepStatus, TimelineStep } from "../types";

function CheckIcon() {
  return (
    <svg width="8" height="8" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path d="M2 6.5 4.8 9.2 10 3.4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

function statusClass(status: StepStatus): string {
  if (status === "done") return "done";
  if (status === "running") return "run";
  if (status === "failed") return "fail";
  if (status === "timeout") return "timeout";
  return "wait";
}

export function Timeline({ steps }: { steps: TimelineStep[] }) {
  if (steps.length === 0) {
    return (
      <div className="tl">
        <p className="event-empty">提交问题后，协作节点会在此由灰变为绿、再变为深绿。</p>
      </div>
    );
  }

  return (
    <div className="tl">
      {steps.map((step, idx) => (
        <div className={`tl-item${idx === steps.length - 1 ? " last" : ""}`} key={step.id}>
          <div className="tl-rail">
            <span className={`tl-dot ${statusClass(step.status)}`}>
              {step.status === "done" ? <CheckIcon /> : null}
              {step.status === "running" ? (
                <span
                  style={{
                    width: 5,
                    height: 5,
                    borderRadius: 999,
                    background: "#fff",
                    display: "block",
                  }}
                />
              ) : null}
            </span>
            {idx !== steps.length - 1 ? <span className="tl-stem" /> : null}
          </div>
          <div className="tl-body">
            <div className="tl-title">
              {step.title}
              <span className="pill ghost" style={{ fontSize: 10 }}>
                {step.agentLabel}
              </span>
              <span style={{ flex: 1 }} />
              {step.duration ? (
                <span className="mono muted" style={{ fontSize: 11, fontWeight: 400 }}>
                  {step.duration}
                </span>
              ) : null}
            </div>
            <div className="tl-desc">{step.desc}</div>
            {step.skills.length > 0 ? (
              <div className="pills" style={{ marginTop: 8 }}>
                {step.skills.map((sk, i) => (
                  <span
                    key={`${sk.name}-${i}`}
                    className={`pill ${sk.active ? "" : "ghost"} mono`}
                    style={{ fontSize: 10 }}
                  >
                    {sk.name}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}
