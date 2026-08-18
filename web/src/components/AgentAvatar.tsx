const COLORS: Record<string, string> = {
  咨: "var(--sage)",
  诊: "var(--wood-deep)",
  研: "var(--mist)",
};

export function AgentAvatar({ label }: { label: string }) {
  return (
    <span className="avatar" style={{ background: COLORS[label] ?? "var(--sage)" }}>
      {label}
    </span>
  );
}

export function AgentAvatarGroup({ count }: { count: number }) {
  const labels = ["咨", "诊", "研"].slice(0, Math.max(1, count));
  return (
    <div className="avatars">
      {labels.map((l) => (
        <AgentAvatar key={l} label={l} />
      ))}
    </div>
  );
}
