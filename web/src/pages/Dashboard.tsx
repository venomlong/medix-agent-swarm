import { useEffect, useState } from "react";
import { getRuntimeStats, USE_MOCK } from "../api/client";
import { DASHBOARD } from "../mock/data";
import type { RuntimeStatsPayload } from "../types";

function formatTokenCount(n: number): string {
  return Math.max(0, Math.round(n)).toLocaleString("zh-CN");
}

function formatYuan(cost: number): string {
  if (!Number.isFinite(cost) || cost <= 0) return "¥0";
  if (cost >= 1) return `¥${cost.toFixed(2)}`;
  if (cost >= 0.01) return `¥${cost.toFixed(3)}`;
  return `¥${cost.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
}

const EMPTY: RuntimeStatsPayload = {
  scope: "current_process",
  label: "本次服务启动后",
  started_at: "",
  uptime_s: 0,
  chat_count: 0,
  swarm_count: 0,
  single_count: 0,
  error_count: 0,
  timeout_count: 0,
  swarm_share: 0,
  avg_latency: "—",
  swarm_latency: "—",
  single_latency: "—",
  auto_fix: 0,
  disclaimer_fix: 0,
  emergency_fix: 0,
  total_tokens: 0,
  total_cost: 0,
  llm_calls: 0,
};

export function Dashboard() {
  const [stats, setStats] = useState<RuntimeStatsPayload | null>(USE_MOCK ? null : EMPTY);
  const [error, setError] = useState("");
  const mock = USE_MOCK;

  useEffect(() => {
    if (mock) return;
    let cancelled = false;
    getRuntimeStats()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [mock]);

  const chatCount = mock ? DASHBOARD.todaySessions : stats?.chat_count ?? 0;
  const swarmShare = mock ? DASHBOARD.swarmShare : stats?.swarm_share ?? 0;
  const singleShare = Math.max(0, 100 - swarmShare);
  const avgLatency = mock ? DASHBOARD.avgLatency : stats?.avg_latency ?? "—";
  const swarmLatency = mock ? DASHBOARD.swarmLatency : stats?.swarm_latency ?? "—";
  const singleLatency = mock ? DASHBOARD.singleLatency : stats?.single_latency ?? "—";
  const autoFix = mock ? DASHBOARD.autoFix : stats?.auto_fix ?? 0;
  const disclaimerFix = mock ? DASHBOARD.disclaimerFix : stats?.disclaimer_fix ?? 0;
  const emergencyFix = mock ? DASHBOARD.emergencyFix : stats?.emergency_fix ?? 0;
  const totalTokens = mock ? DASHBOARD.totalTokens : stats?.total_tokens ?? 0;
  const totalCost = mock ? DASHBOARD.totalCost : stats?.total_cost ?? 0;
  const llmCalls = mock ? DASHBOARD.llmCalls : stats?.llm_calls ?? 0;
  const swarmCount = stats?.swarm_count ?? 0;
  const singleCount = stats?.single_count ?? 0;
  const barHeight = chatCount > 0 ? 80 : 8;

  return (
    <main className="page">
      <div className="page-head">
        <h1>总览仪表盘</h1>
        <span className="demo-tag">{mock ? "示意数据" : "本次服务启动后"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "会话量、Swarm 占比、平均耗时与安全自动修复次数，一眼可知系统运行状态。"
          : "计数来自当前 webapi 进程内存，服务重启后归零。Token / 成本 / LLM 调用次数同样为本进程累计。"}
      </p>
      {error ? <div className="card empty-hint">{error}</div> : null}

      <div className="grid-4">
        <div className="card kpi">
          <div className="k">{mock ? "今日会话" : "会话次数"}</div>
          <div className="n">{chatCount}</div>
          <div className="s">
            {mock ? `本周累计 ${DASHBOARD.weekSessions}` : `错误 ${stats?.error_count ?? 0} · 超时 ${stats?.timeout_count ?? 0}`}
          </div>
        </div>
        <div className="card kpi">
          <div className="k">Swarm 协作占比</div>
          <div className="n">{swarmShare}%</div>
          <div className="s">{mock ? "其余为单 Agent 快速应答" : `Swarm ${swarmCount} · 单 Agent ${singleCount}`}</div>
        </div>
        <div className="card kpi">
          <div className="k">平均响应耗时</div>
          <div className="n">{avgLatency}</div>
          <div className="s">
            Swarm {swarmLatency} · 单 Agent {singleLatency}
          </div>
        </div>
        <div className="card kpi">
          <div className="k">安全自动修复</div>
          <div className="n">{autoFix} 次</div>
          <div className="s">
            免责声明 {disclaimerFix} · 就医提醒 {emergencyFix}
          </div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 12 }}>
        <div className="card kpi">
          <div className="k">累计 Token</div>
          <div className="n">{formatTokenCount(totalTokens)}</div>
          <div className="s">LLM 调用 {llmCalls} 次 · 本进程累计</div>
        </div>
        <div className="card kpi">
          <div className="k">累计成本</div>
          <div className="n">{formatYuan(totalCost)}</div>
          <div className="s">{mock ? "示意：按当前模型定价估算" : "按当前模型定价估算 · 人民币"}</div>
        </div>
      </div>

      <div className="grid-2" style={{ marginTop: 16 }}>
        <div className="card chart-card">
          <strong style={{ fontSize: 13 }}>{mock ? "近 7 天会话量（次）" : "当前进程会话量"}</strong>
          {mock ? (
            <div className="bars">
              {DASHBOARD.bars.map((b) => (
                <div className="bar-col" key={b.day}>
                  <span className="bar-v">{b.value}</span>
                  <div className={`bar${b.peak ? " peak" : ""}`} style={{ height: `${b.height}%` }} />
                  <span className="bar-d">{b.day}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="bars">
              <div className="bar-col">
                <span className="bar-v">{chatCount}</span>
                <div className="bar peak" style={{ height: `${barHeight}%` }} />
                <span className="bar-d">本次</span>
              </div>
            </div>
          )}
          <p className="caption" style={{ marginTop: 4 }}>
            {mock ? "横轴：日期 · 纵轴：会话次数 · 示意数据" : "无按日历史库 · 仅显示本次启动后的累计次数"}
          </p>
        </div>

        <div className="card chart-card">
          <strong style={{ fontSize: 13 }}>{mock ? "路由模式分布（本周）" : "路由模式分布（本次启动后）"}</strong>
          <div style={{ marginTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
              <span className="muted">单 Agent 快速应答</span>
              <span className="mono">{singleShare}%</span>
            </div>
            <div className="meter">
              <i style={{ width: `${singleShare}%`, background: "var(--sage)" }} />
            </div>
          </div>
          <div style={{ marginTop: 16 }}>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12.5 }}>
              <span className="muted">Swarm 多 Agent 协作</span>
              <span className="mono">{swarmShare}%</span>
            </div>
            <div className="meter">
              <i style={{ width: `${swarmShare}%`, background: "var(--wood-deep)" }} />
            </div>
          </div>
          <p className="caption">路由由 LeadAgent 任务分解结果决定{mock ? " · 示意数据" : ""}</p>
        </div>
      </div>
    </main>
  );
}
