import { NavLink, Outlet } from "react-router-dom";
import { USE_MOCK } from "../api/client";
import { SESSION_ID } from "../mock/data";
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
            {USE_MOCK ? `会话 ${SESSION_ID}` : "API /api"}
          </span>
        </div>
      </nav>
      <div className="app-main">
        <Outlet />
      </div>
    </div>
  );
}
