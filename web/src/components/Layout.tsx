import { NavLink, Outlet } from "react-router-dom";
import { USE_MOCK } from "../api/client";
import { LeafLogo } from "./LeafLogo";

const LINKS = [
  { to: "/", label: "对话工作台", end: true },
  { to: "/dashboard", label: "仪表盘" },
  { to: "/memory", label: "会话记忆" },
  { to: "/knowledge", label: "知识库" },
  { to: "/safety", label: "安全质量" },
];

export function Layout() {
  return (
    <div className="app-shell">
      <nav className="nav" aria-label="主导航">
        <div className="nav-inner">
          <NavLink to="/" className="brand" end>
            <LeafLogo />
            <span className="brand-name">MediX 智愈</span>
          </NavLink>
          <div className="nav-links">
            {LINKS.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.end}
                className={({ isActive }) => (isActive ? "active" : undefined)}
              >
                {l.label}
              </NavLink>
            ))}
          </div>
          <span className="nav-extra pill ghost" style={{ fontSize: 11 }}>
            {USE_MOCK ? "示意 Mock" : "真实 SSE"}
          </span>
        </div>
      </nav>
      {USE_MOCK ? (
        <div className="mock-banner" role="status">
          当前为本地示意数据（VITE_USE_MOCK=true）。急症警示、知识库来源与 Token 用量不会走真实 /api/chat。去掉该环境变量后重启前端即可连后端。
        </div>
      ) : null}
      <div className="app-main">
        <Outlet />
      </div>
    </div>
  );
}
