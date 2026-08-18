import { AgentAvatarGroup } from "./AgentAvatar";

type Props = {
  agentCount?: number;
};

/**
 * Swarm 90 秒超时兜底。演示默认走成功路径，此组件在 timedOut 时渲染。
 * 对齐 timeout_occurred：时间线未完成节点为赭红空心点，答案区明示部分结果。
 */
export function TimeoutFallback({ agentCount = 0 }: Props) {
  return (
    <aside className="timeout-card" role="status">
      <div className="answer-head">
        <AgentAvatarGroup count={Math.max(agentCount, 1)} />
        <span className="status">
          <i style={{ border: "1.5px solid var(--rust)", background: "transparent" }} />
          超时
        </span>
        <span style={{ flex: 1 }} />
        <span className="mono muted" style={{ fontSize: 12 }}>
          90.0s
        </span>
      </div>
      <h4>基于 {agentCount} 个 Agent 的部分结果</h4>
      <p>
        协作超过 90 秒仍未全部完成。未完成的 Agent 节点已转为超时态（赭红空心点）。以下为已完成部分的汇总，完整结论可能不充分。
      </p>
    </aside>
  );
}
