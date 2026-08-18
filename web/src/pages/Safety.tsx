import { useEffect, useState } from "react";
import { getSafetyFixes, USE_MOCK } from "../api/client";
import { FIX_RECORDS, SAFETY } from "../mock/data";
import type { FixRecord } from "../types";

export function Safety() {
  const mock = USE_MOCK;
  const [records, setRecords] = useState<FixRecord[]>(mock ? FIX_RECORDS : []);
  const [assertions, setAssertions] = useState<string[]>(
    mock
      ? ["输出须含免责声明；检出胸痛、言语不清、一侧肢体无力等关键词时须附加就医提醒。当前断言全部通过。"]
      : []
  );
  const [label, setLabel] = useState(mock ? "" : "本次服务启动后的 AutoFixer 记录（无持久历史库）");
  const [error, setError] = useState("");

  useEffect(() => {
    if (mock) return;
    let cancelled = false;
    getSafetyFixes()
      .then((data) => {
        if (cancelled) return;
        setRecords(data.records ?? []);
        setAssertions(data.assertions ?? []);
        setLabel(data.label ?? "");
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [mock]);

  return (
    <main className="page">
      <div className="page-head">
        <h1>安全与质量</h1>
        <span className="demo-tag">{mock ? "示意数据" : "本次进程内"}</span>
      </div>
      <p className="page-lede">
        {mock
          ? "约束验证、AutoFixer 修复日志与测试套件结果（Harness 可视化）。"
          : "AutoFixer 没有持久日志库。这里只展示当前 webapi 进程内实际发生过的修复；重启后为空。"}
      </p>
      {error ? <div className="card empty-hint">{error}</div> : null}

      {mock ? (
        <div className="grid-2" style={{ gap: 8, marginBottom: 16 }}>
          <div className="card kpi">
            <div className="k">测试套件通过</div>
            <div className="n">
              {SAFETY.testsPassed} / {SAFETY.testsTotal}
            </div>
          </div>
          <div className="card kpi">
            <div className="k">安全断言通过</div>
            <div className="n">{SAFETY.assertionRate}</div>
          </div>
        </div>
      ) : (
        <div className="grid-2" style={{ gap: 8, marginBottom: 16 }}>
          <div className="card kpi">
            <div className="k">本次进程修复次数</div>
            <div className="n">{records.length}</div>
          </div>
          <div className="card kpi">
            <div className="k">持久历史库</div>
            <div className="n" style={{ fontSize: 22 }}>
              无
            </div>
          </div>
        </div>
      )}

      <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
        安全断言
      </div>
      <div className="card" style={{ padding: "12px 16px", marginBottom: 16 }}>
        {assertions.length ? (
          assertions.map((a) => (
            <p key={a} style={{ fontSize: 13.5 }}>
              {a}
            </p>
          ))
        ) : (
          <p style={{ fontSize: 13.5 }}>输出须含免责声明；高危关键词须附加就医提醒。</p>
        )}
      </div>

      <div className="muted" style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>
        AutoFixer 修复记录
      </div>
      {records.length === 0 ? (
        <div className="card empty-hint">{label || "本次进程内尚无自动修复记录。"}</div>
      ) : (
        <div className="card fix-list">
          {records.map((r) => (
            <div className="row-item" key={`${r.time}-${r.detail}`}>
              <span className="mono muted" style={{ fontSize: 11 }}>
                {r.time}
              </span>
              <span className={`pill${r.kind === "就医提醒" ? " clay" : ""}`} style={{ fontSize: 10 }}>
                {r.kind}
              </span>
              <span style={{ fontSize: 12.5 }}>{r.detail}</span>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
