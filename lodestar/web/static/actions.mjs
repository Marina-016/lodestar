import { escape as esc } from "./markdown.mjs";

// Only host receipts count; never infer actions from generated answer text.
export function actionHTML(receipts, { pending = false } = {}) {
  if (!Array.isArray(receipts)) return ""; // Legacy messages have no audited ledger.
  const rows = receipts.flatMap((r) => {
    if (r.action === "create_task") {
      const label = r.status === "submitted" ? "已提交任务" : "任务已创建，未启动";
      return [`<li><button class="subtle" data-receipt-item="${esc(r.id)}">${label}：${esc(r.name)}</button>${r.external_retrieval === "blocked" ? "<span>不联网检索</span>" : ""}</li>`];
    }
    if (r.action === "manage_memory") {
      if (r.deleted) return ["<li>已遗忘记忆</li>"];
      const label = r.change === "updated" ? "已更新记忆" : "已新增记忆";
      return [`<li><button class="subtle" data-receipt-memory="${esc(r.id)}">${label} · 查看详情</button></li>`];
    }
    if (r.action === "record_learning") return ["<li>已记录学习反馈 · 不代表掌握</li>"];
    return [];
  });
  if (!rows.length) return "";
  return `<aside class="action-receipts" aria-label="实际执行记录"><small>执行记录${pending ? " · 本轮仍在运行" : ""}</small><ul>${rows.join("")}</ul></aside>`;
}
