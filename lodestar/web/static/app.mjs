import { escape as esc, markdown, safeUrl } from "./markdown.mjs";
import { sourceGroups } from "./sources.mjs";
import { actionHTML } from "./actions.mjs";
const $ = (s) => document.querySelector(s),
  icon = (name) => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const token = $('meta[name="lodestar-token"]').content;
let submitting = false,
  drawerFocus = null;
let state = null,
  itemId = "main",
  generation = 0,
  activeRun = null,
  drawerKind = "",
  showArchived = false,
  noteId = null,
  toastTimer;
try {
  itemId = localStorage.getItem("lodestar-item") || "main";
} catch {}
const labels = {
  search_papers: "正在检索论文",
  discover_papers: "正在发现近期论文",
  search_web: "正在搜索网页",
  read_paper: "正在阅读论文",
  read_webpage: "正在阅读网页",
  verify_paper: "正在核查发表信息",
  project_context: "正在查看项目资料",
  create_task: "正在派发任务",
  list_tasks: "正在查看任务进度",
  read_task: "正在读取任务结果",
  read_memory: "正在回顾记忆",
  manage_memory: "正在更新记忆",
  record_learning: "正在记录学习反馈",
};
const runLabels = { queued: "排队中", running: "运行中", cancelling: "停止中", completed: "本轮完成", error: "需要重试", cancelled: "已停止", interrupted: "已中断" };
const scopeLabels = {personal: "个人 · 共享", project: "项目", task: "仅当前任务"};
const masteryLabels = {unknown: "掌握情况未验证", can_explain: "有讲解证据", can_implement: "有实现证据", can_apply: "有应用证据"};
let learningId = null;
function projectOptions(value) {
  return '<option value="">未绑定项目</option>' + state.projects.map((p) => `<option value="${p.id}" ${p.id === value ? "selected" : ""}>${esc(p.name)}</option>`).join("");
}
function taskStatus(t) {
  if (t.status !== "active") return t.status === "completed" ? "已完成" : "已归档";
  if (t.run_status === "running" && t.progress) {
    try { return labels[JSON.parse(t.progress)] || "运行中"; } catch {}
  }
  return runLabels[t.run_status] || "待开始";
}
async function api(path, body) {
  const response = await fetch(path, {
    headers: body
      ? { "Content-Type": "application/json", "X-Lodestar-Token": token }
      : {},
    method: body ? "POST" : "GET",
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求未完成");
  return data;
}
function toast(message) {
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
}
function setStatus(text, busy = false) {
  $("#run-status").textContent = text;
  $("#run-status").classList.toggle("busy", busy);
  $("#composer").setAttribute("aria-busy", String(busy));
}
function nearBottom() {
  const el = $("#scroll-area");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 180;
}
function updateLatest() {
  $("#scroll-latest").hidden = !state?.messages.length || nearBottom();
}
function scrollBottom(force = false) {
  const el = $("#scroll-area");
  if (force || nearBottom()) el.scrollTop = el.scrollHeight;
  updateLatest();
}
function busy() {
  return !!activeRun || submitting;
}
function controls() {
  const inactive = !state || state.item.status !== "active";
  const paused = state && !state.enabled && state.mode !== "mock";
  const stopping = activeRun?.status === "cancelling";
  $("#send").disabled = activeRun ? stopping : submitting || inactive || paused || !$("#prompt").value.trim();
  $("#prompt").disabled = inactive;
  $("#thinking").disabled = !state;
  $("#model-settings").disabled = !state;
  $("#send").classList.toggle("is-busy", submitting || stopping);
  $("#send use").setAttribute("href", activeRun ? "#i-stop" : "#i-arrow");
  $("#send").setAttribute("aria-label", activeRun ? stopping ? "正在停止" : "停止回答" : "发送问题");
  $("#send").title = activeRun ? stopping ? "等待当前请求返回后停止" : "停止回答"
    : submitting ? "正在提交"
    : inactive ? "请先恢复任务"
      : paused ? "点击模型名称，启用模型调用"
        : $("#prompt").value.trim() ? "发送问题" : "先输入一个研究问题";
}
function renderMessages() {
  $("#messages").innerHTML = state.messages
    .map((m) =>
      m.role === "user"
        ? `<article class="message message-user"><div class="user-bubble">${esc(m.content)}</div></article>`
        : assistant(
            m.content,
            m.metadata?.model_configuration,
            m.kind === "error",
            m.id,
            m.metadata?.action_receipts,
          ),
    )
    .join("");
  $("#main").classList.toggle("is-empty", !state.messages.length);
  $("#welcome").hidden = !!state.messages.length;
  $("#messages").hidden = !state.messages.length;
  $("#welcome h1").textContent =
    itemId === "main" ? "今天，想研究什么？" : state.item.name;
  $("#welcome>p").innerHTML =
    itemId === "main"
      ? "发现值得读的论文，理解新方法，<br class=\"mobile-break\">把想法带到正在做的项目中。"
      : "从第一个问题开始，围绕这个课题积累资料、讨论和记忆。";
}
function assistant(text, config, error = false, id = "draft", receipts) {
  const count = id === "draft" ? 0 : sourcesFor(id).references.length;
  const answer = text ? markdown(text) : '<div class="typing-dots" aria-hidden="true"><span></span><span></span><span></span></div>';
  const actions = id === "draft" ? "" : `<div class="message-actions"><button class="subtle copy-answer" data-id="${id}" title="复制完整回答">${icon("copy")}<span>复制</span></button><button class="subtle inspect-answer" data-id="${id}" title="查看本轮来源与上下文">${icon("paper")}<span>来源与上下文</span>${count ? `<span class="source-count">${count}</span>` : ""}</button></div>`;
  return `<article class="message message-assistant ${error ? "message-error" : ""}" data-message="${id}"><div class="assistant-meta"><span class="assistant-mark">${icon("star")}</span><span>Lodestar</span><small>${esc(config?.model || state.configuration.model)}</small></div><div class="answer">${answer}</div><div class="execution">${actionHTML(receipts, { pending: id === "draft" })}</div>${actions}</article>`;
}
function render() {
  $("#space-name").textContent = state.item.name;
  $("#space-subtitle").textContent =
    itemId === "main" ? "持续研究" : "任务";
  $("#task-options").hidden = itemId === "main";
  $("#home").classList.toggle("active", itemId === "main");
  $("#home").setAttribute("aria-current", itemId === "main" ? "page" : "false");
  renderTasks();
  renderModelControls();
  const project = state.projects.find((p) => p.id === state.item.project_id);
  $("#scope-label").textContent = `共享个人记忆${project ? " · " + project.name : ""} · 当前任务对话`;
  renderMessages();
  controls();
  if (drawerKind) renderDrawer();
}
function renderTasks() {
  const tasks = state.items.filter((t) => t.id !== "main");
  $("#task-count").textContent = tasks.filter((t) => t.status !== "archived").length;
  $("#task-list").innerHTML = tasks
    .filter((t) => showArchived || t.status !== "archived")
    .map(
      (t) =>
        `<button class="nav-item ${t.id === itemId ? "active" : ""} ${t.status === "completed" ? "task-completed" : ""}" data-item="${esc(t.id)}" aria-current="${t.id === itemId ? "page" : "false"}">${icon("task")}<span class="task-name">${esc(t.name)}<small class="task-state">${esc(taskStatus(t))}</small></span></button>`,
    )
    .join("");
  $("#tasks-empty").hidden = tasks.some((t) => t.status !== "archived");
  $("#archived-toggle").hidden = !tasks.some((t) => t.status === "archived");
  $("#archived-toggle").textContent = showArchived
    ? "收起已归档任务"
    : "查看已归档任务";
}
async function load(id = itemId) {
  const epoch = ++generation;
  activeRun = null;
  submitting = false;
  setStatus("");
  try {
    const data = await api("/api/state?item=" + encodeURIComponent(id));
    if (epoch !== generation) return;
    state = data;
    itemId = id;
    try {
      localStorage.setItem("lodestar-item", id);
    } catch {}
    render();
    closeNavigation();
    scrollBottom(true);
    if (state.run && ["queued", "running", "cancelling"].includes(state.run.status))
      startPoll(state.run);
    else if (state.run?.status === "cancelled")
      setStatus("本轮已停止，已保留生成内容。");
    else if (state.run?.status === "interrupted")
      setStatus("上次运行已中断，未自动重试。");
    else if (!state.enabled && state.mode !== "mock")
      setStatus("模型调用已暂停 · 点击模型名称可启用");
    else if (state.mode === "mock") setStatus("离线演示模式 · 不调用真实模型");
  } catch (error) {
    if (epoch !== generation) return;
    if (id !== "main") return load("main");
    toast(error.message);
    setStatus("本地服务未连接，请刷新重试");
  }
}
function startPoll(run) {
  activeRun = run;
  controls();
  $("#main").classList.remove("is-empty");
  $("#welcome").hidden = true;
  $("#messages").hidden = false;
  let after = 0,
    text = "",
    receipts = [],
    epoch = generation;
  $("#messages").insertAdjacentHTML(
    "beforeend",
    assistant("", JSON.parse(run.config), false, "draft", []),
  );
  setStatus(run.status === "cancelling" ? "正在停止，等待当前请求返回…" : "正在理解问题…", true);
  scrollBottom(true);
  const tick = async () => {
    if (epoch !== generation) return;
    try {
      const data = await api(
        `/api/events?run=${encodeURIComponent(run.id)}&after=${after}`,
      );
      if (epoch !== generation) return;
      activeRun = data.run;
      controls();
      const followStream = nearBottom();
      for (const event of data.events) {
        after = event.id;
        if (event.kind === "text") {
          text += event.data;
          const el = $('[data-message="draft"] .answer');
          if (el) el.innerHTML = markdown(text);
          setStatus("正在回答…", true);
        } else if (event.kind === "action") {
          receipts.push(event.data);
          const el = $('[data-message="draft"] .execution');
          if (el) el.innerHTML = actionHTML(receipts, { pending: true });
        } else if (event.kind === "progress")
          setStatus((labels[event.data] || "正在准备资料") + "…", true);
      }
      if (data.run.status === "cancelling") setStatus("正在停止，等待当前请求返回…", true);
      scrollBottom(followStream);
      if (["queued", "running", "cancelling"].includes(data.run.status)) {
        setTimeout(tick, 450);
        return;
      }
      const result = JSON.parse(data.run.result || "{}");
      const response = await api(
        "/api/state?item=" + encodeURIComponent(itemId),
      );
      if (epoch !== generation) return;
      const follow = nearBottom();
      activeRun = null;
      state = response;
      render();
      if (
        result.answer &&
        !state.messages.some(
          (m) => m.role === "assistant" && m.content === result.answer,
        )
      )
        $("#messages").insertAdjacentHTML(
          "beforeend",
          assistant(
            result.answer,
            JSON.parse(run.config),
            data.run.status === "error",
          ),
        );
      setStatus(
        data.run.status === "completed"
          ? ""
          : data.run.status === "cancelled" ? "本轮已停止，已保留生成内容。"
          : data.run.status === "interrupted"
            ? "运行已中断；没有自动重试"
            : "本轮未完成，可以修改问题后重试",
      );
      scrollBottom(follow);
    } catch (error) {
      if (epoch !== generation) return;
      setStatus("连接中断；后台运行可能仍在继续，刷新可恢复");
      activeRun = null;
      controls();
      toast(error.message);
    }
  };
  tick();
}
$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (activeRun) {
    if (activeRun.status === "cancelling") return;
    const run = activeRun, epoch = generation;
    try {
      const result = await api("/api/cancel", { item: itemId, run: run.id });
      if (epoch !== generation || activeRun?.id !== run.id) return;
      activeRun.status = result.status;
      controls();
      setStatus(result.status === "cancelling" ? "正在停止，等待当前请求返回…" : "正在保存本轮结果…", true);
    } catch (error) { toast(error.message); }
    return;
  }
  const message = $("#prompt").value.trim();
  if (!message || busy() || !state) return;
  const id = itemId,
    epoch = generation;
  submitting = true;
  $("#send").disabled = true;
  setStatus("正在提交…", true);
  try {
    const request = await api("/api/send", {
      item: id,
      message,
      key: crypto.randomUUID(),
    });
    if (epoch !== generation) return;
    submitting = false;
    $("#prompt").value = "";
    $("#prompt").style.height = "auto";
    state.messages.push({ role: "user", content: message });
    renderMessages();
    startPoll({ id: request.id, status: "queued", config: JSON.stringify(state.configuration) });
  } catch (error) {
    if (epoch !== generation) return;
    submitting = false;
    setStatus(error.message);
    controls();
    toast(error.message);
  }
});
$("#prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!busy()) $("#composer").requestSubmit();
  }
});
$("#prompt").addEventListener("input", () => {
  $("#prompt").style.height = "auto";
  $("#prompt").style.height = Math.min($("#prompt").scrollHeight, 190) + "px";
  controls();
});
$(".suggestions").addEventListener("click", (event) => {
  const button = event.target.closest("[data-prompt]");
  if (!button) return;
  $("#prompt").value = button.dataset.prompt;
  $("#prompt").dispatchEvent(new Event("input"));
  $("#prompt").focus();
});
$("#home").onclick = () => load("main");
$("#task-list").onclick = (e) => {
  const b = e.target.closest("[data-item]");
  if (b) load(b.dataset.item);
};
$("#archived-toggle").onclick = () => {
  showArchived = !showArchived;
  render();
};
$("#new-task").onclick = () => {
  $("#task-name").value = "";
  $("#task-project").innerHTML = projectOptions(state.item.project_id);
  $("#task-dialog").showModal();
};
$("#task-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const task = await api("/api/items", { name: $("#task-name").value, project_id: Number($("#task-project").value) || null });
    $("#task-dialog").close();
    closeDrawer();
    await load(task.id);
  } catch (error) {
    toast(error.message);
  }
};
$("#task-options").onclick = () => {
  $("#manage-name").value = state.item.name;
  $("#manage-status").value = state.item.status;
  $("#manage-project").innerHTML = projectOptions(state.item.project_id);
  $("#manage-dialog").showModal();
};
$("#manage-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/item", {
      item: itemId,
      name: $("#manage-name").value,
      status: $("#manage-status").value,
      ...((Number($("#manage-project").value) || null) !== state.item.project_id ? {project_id: Number($("#manage-project").value) || null} : {}),
    });
    $("#manage-dialog").close();
    await load();
  } catch (error) {
    toast(error.message);
  }
};
function renderModelControls() {
  const model = state.configuration.model;
  $("#model-label").textContent = model;
  $("#model-settings").dataset.enabled = String(state.enabled);
  $("#model-settings").title = model + " · 模型与推理设置";
  const reasoning = state.reasoning;
  const label = reasoning.options.find((o) => o.value === reasoning.value)?.label || "服务默认";
  const compact = label.replace("当前思考预算 · ", "");
  $("#thinking span").textContent = (label.startsWith("当前思考预算") ? "预算 · " : "推理 · ") + compact;
  $("#thinking").title = "推理设置 · " + label;
}
let modelCatalog = {}, settingsGeneration = 0, catalogGeneration = 0;
function selectedModel() {
  return $("#model-select").value === "__custom__"
    ? $("#model-id").value.trim() : $("#model-select").value;
}
function modelOptions(ids, selected) {
  $("#model-select").innerHTML = [...new Set([selected, ...ids])]
    .map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("") +
    '<option value="__custom__">自定义模型 ID…</option>';
  $("#model-select").value = selected;
}
function reasoningOptions(capability, value = capability.value) {
  $("#reasoning-setting").innerHTML = capability.options.map((o) =>
    `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  $("#reasoning-setting").value = capability.options.some((o) => o.value === value) ? value : "default";
  $("#reasoning-note").textContent = capability.note;
}
async function openModelSettings() {
  if (!state) return;
  const currentItem = itemId, revision = ++catalogGeneration;
  $("#model-id").value = state.configuration.model;
  $("#model-id").hidden = true;
  modelOptions(state.models, state.configuration.model);
  reasoningOptions(state.reasoning);
  $("#enabled-setting").checked = state.enabled;
  $("#catalog-status").textContent = "正在读取服务端模型目录…";
  $("#settings-dialog").showModal();
  try {
    const catalog = await api("/api/models", { item: currentItem });
    if (revision !== catalogGeneration || currentItem !== itemId || !$("#settings-dialog").open) return;
    modelCatalog = catalog.reasoning;
    const selected = selectedModel();
    if ($("#model-select").value !== "__custom__") modelOptions(catalog.models, selected);
    $("#catalog-status").textContent = `已读取 ${catalog.models.length} 个模型；目录不代表免费额度或调用权限。`;
  } catch (error) {
    if (revision === catalogGeneration)
      $("#catalog-status").textContent = "目录读取失败，仍可输入模型 ID。";
  }
}
async function chooseModel() {
  const custom = $("#model-select").value === "__custom__";
  $("#model-id").hidden = !custom;
  if (custom) $("#model-id").focus();
  const model = selectedModel(), revision = ++settingsGeneration;
  if (!model) return;
  if (model === state.configuration.model) return reasoningOptions(state.reasoning);
  let capability = modelCatalog[model];
  if (!capability) {
    try { capability = await api("/api/reasoning", {model}); }
    catch { return; }
    if (revision !== settingsGeneration) return;
  }
  reasoningOptions(capability, capability.efforts[0] || "default");
}
$("#model-settings").onclick = openModelSettings;
$("#thinking").onclick = openModelSettings;
$("#model-select").onchange = chooseModel;
$("#model-id").oninput = chooseModel;
$("#settings-form").onsubmit = async (e) => {
  e.preventDefault();
  const targetItem = itemId;
  try {
    const updated = await api("/api/item", {
      item: targetItem,
      settings: {
        model: selectedModel(),
        reasoning: $("#reasoning-setting").value,
        enabled: $("#enabled-setting").checked,
      },
    });
    $("#settings-dialog").close();
    if (targetItem !== itemId) return;
    // Refresh saved settings even while the existing answer keeps streaming with
    // its frozen run config. Never replace its draft or restart its polling.
    state.configuration = updated.configuration;
    state.reasoning = updated.reasoning;
    state.enabled = updated.enabled;
    state.item.settings = updated.item.settings;
    renderModelControls();
    if (busy()) toast("已保存，下一轮生效");
    else await load();
  } catch (error) {
    toast(error.message);
  }
};
function sourcesFor(messageId = null) {
  const message = messageId
    ? state.messages.find((m) => String(m.id) === String(messageId))
    : [...state.messages].reverse().find((m) => m.role === "assistant");
  return sourceGroups(message, state.messages);
}
let sourceMessage = null;
function sourceHTML(source, index = 0) {
  const url = safeUrl(source.url);
  const host = url ? new URL(url).hostname.replace(/^www\./, "") : "";
  const depth = source.observed === false ? "未关联检索记录" : source.read_depth === "full" ? "正文片段" : source.read_depth === "web" ? "网页片段" : source.read_depth === "abstract" ? "已读摘要" : "检索候选";
  return `<article class="source-item"><span class="source-index">${index + 1}</span><div class="source-body">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(source.title || source.url)}${icon("out")}</a>` : esc(source.title || "来源")}<div class="source-host">${esc(host)}</div><div class="source-meta"><span class="source-depth">${depth}</span><span>${esc(source.date?.slice(0, 10) || "日期未标注")}</span>${source.venue ? `<span>${esc(source.venue)}</span>` : ""}</div></div></article>`;
}
function openDrawer(kind) {
  drawerFocus = $("#sidebar").classList.contains("open") ? $("#menu") : document.activeElement;
  closeNavigation();
  $(".app").inert = true;
  drawerKind = kind;
  sourceMessage = null;
  renderDrawer();
  $("#drawer").hidden = false;
  $("#drawer-backdrop").hidden = false;
  $("#close-drawer").focus();
}
function closeDrawer() {
  if (!drawerKind) return;
  $(".app").inert = false;
  drawerFocus?.focus();
  drawerKind = "";
  $("#drawer").hidden = true;
  $("#drawer-backdrop").hidden = true;
}
function renderDrawer() {
  const target = $("#drawer-content");
  $("#drawer-title").textContent = {
    context: "当前上下文",
    memory: "记忆",
    tasks: "任务",
    projects: "项目",
  }[drawerKind];
  $("#drawer-eyebrow").textContent = state.item.name.toUpperCase();
  if (drawerKind === "context") {
    const { references: sources, retrieval } = sourcesFor(sourceMessage);
    const message = sourceMessage
      ? state.messages.find((m) => String(m.id) === String(sourceMessage))
      : [...state.messages].reverse().find((m) => m.role === "assistant");
    const used = message?.metadata?.web_context?.memory;
    const config =
      message?.metadata?.model_configuration || state.configuration;
    const web = message?.metadata?.web_context;
    target.innerHTML = `<section class="drawer-section"><h3>${message ? "所选回答的范围" : "当前任务范围"}</h3><div class="detail-row"><span>当前任务</span><span>${esc(state.item.name)}</span></div><div class="detail-row"><span>对话</span><span>当前任务 · 变更边界后的近期记录</span></div><div class="detail-row"><span>记忆</span><span>${used ? used.length + " 条本轮带入" : state.context_notes.length + " 条当前可用"}</span></div><div class="detail-row"><span>模型</span><span>${esc(config.model)}</span></div>${web?.history_after ? '<p class="panel-info">本轮已跳过记忆变更前的模型上下文，历史聊天仍可查看。</p>' : ""}<p class="panel-info">同一用户共享个人记忆，同项目共享项目记忆。其他任务的结果可由模型按需读取。</p></section><section class="drawer-section"><h3>项目归属 <button class="subtle" id="register-project">＋ 登记</button></h3><select id="bind-project" aria-label="绑定项目">${projectOptions(state.item.project_id)}</select><p class="fineprint">更换项目会重新建立后续上下文；登记与绑定不会自动读取本地代码。</p></section><section class="drawer-section"><h3>参考来源 <span>${sources.length || ""}</span></h3>${sources.length ? sources.map(sourceHTML).join("") : '<div class="empty-state">这条回答没有引用来源。</div>'}<p class="panel-info">仅列出这条回答引用的链接；引用不代表内容已经核实。</p></section>${retrieval.length ? `<details class="retrieval-records drawer-section"><summary>检索记录 <span>${retrieval.length}</span></summary><p class="panel-info">本轮检索或阅读过、但未在回答中引用的页面。</p>${retrieval.map(sourceHTML).join("")}</details>` : ""}`;
    $("#bind-project").onchange = async (e) => {
      try { await api("/api/item", {item: itemId, project_id: Number(e.target.value) || null}); await load(); }
      catch (error) { toast(error.message); renderDrawer(); }
    };
    $("#register-project").onclick = () => $("#project-dialog").showModal();
  }
  if (drawerKind === "memory") {
    target.innerHTML = `<section class="drawer-section"><h3>个人与项目记忆 <button class="subtle" id="add-note">＋ 添加</button></h3><p>查看所有已保存的事实和偏好。范围决定哪些任务可以使用。</p><input class="memory-search" id="memory-search" type="search" placeholder="搜索记忆" aria-label="搜索记忆"><div id="notes-list"></div></section><section class="drawer-section"><h3>我的学习</h3>${state.learning.length ? state.learning.map((l) => `<details class="learning-row"><summary>${esc(l.technology)}${l.method ? " · " + esc(l.method) : ""}<small>${l.event_count} 条证据 · ${l.paper_count} 篇关联论文 · ${esc(masteryLabels[l.mastery] || l.mastery)}</small></summary>${l.evidence.map((e) => `<article class="learning-evidence"><div class="source-meta">${esc(e.event)} · ${esc(e.created_at.slice(0,10))}</div><p>${esc(e.evidence)}</p>${safeUrl(e.paper_url) ? `<a href="${esc(safeUrl(e.paper_url))}" target="_blank" rel="noopener noreferrer">关联论文 ↗</a>` : ""}<div class="note-actions"><button class="subtle" data-learning="${e.id}" data-action="correct">纠正</button><button class="subtle" data-learning="${e.id}" data-action="revoke">撤销证据</button></div></article>`).join("")}</details>`).join("") : '<div class="empty-state">还没有学习记录。<br>后台研究不代表你已经接触或掌握。</div>'}</section><p class="panel-info">纠正、停用和遗忘会更新后续上下文边界，避免旧回答再次带入失效记忆。已有聊天仍保留；已发送给模型的信息无法撤回。</p>`;
    renderNotes();
    $("#memory-search").oninput = renderNotes;
    $("#add-note").onclick = () => editNote();
  }
  if (drawerKind === "tasks") {
    const tasks = state.items.filter((t) => t.id !== "main" && t.status !== "archived");
    target.innerHTML = `<section class="drawer-section"><h3>各自推进，随时接续 <span>${tasks.length}</span></h3><p>在主对话中派发多个研究任务，也可以进入任一任务继续讨论。</p>${tasks.length ? tasks.map((t) => `<button class="task-card" data-open-task="${esc(t.id)}"><strong>${esc(t.name)}</strong><span>${esc(taskStatus(t))}</span><small>${esc(state.projects.find((p) => p.id === t.project_id)?.name || "未绑定项目")} · 共享个人记忆</small></button>`).join("") : '<div class="empty-state">试试：分别研究 Harness 和 RAG 的近期论文，创建两个任务。</div>'}</section>`;
  }
}
function renderNotes() {
  const q = $("#memory-search").value.toLowerCase();
  const notes = state.notes.filter((n) => n.content.toLowerCase().includes(q));
  $("#notes-list").innerHTML = notes.length
    ? notes
        .map(
          (n) =>
            `<article class="note-item ${n.status === "disabled" ? "disabled" : ""}"><p>${esc(n.content)}</p><div class="source-meta"><span>${n.status === "active" ? "已启用" : "已停用"}</span><span>${esc(scopeLabels[n.scope])}${n.project_id ? " · " + esc(state.projects.find((p) => p.id === n.project_id)?.name || "") : n.item_id ? " · " + esc(state.items.find((t) => t.id === n.item_id)?.name || "") : ""}</span><span>修订 ${n.revision}</span><span>${n.source === "conversation" ? "来自对话" : n.source === "legacy" ? "旧记录保留" : "手动保存"}</span>${n.source_message_id ? `<span>消息 #${n.source_message_id}</span>` : ""}</div><div class="note-actions"><button class="subtle" data-note="${n.id}" data-action="edit">编辑</button><button class="subtle" data-note="${n.id}" data-action="toggle">${n.status === "active" ? "停用" : "恢复"}</button><button class="subtle" data-note="${n.id}" data-action="delete">遗忘</button></div></article>`,
        )
        .join("")
    : '<div class="empty-state">' +
      (q ? "没有找到匹配的记忆。" : "把希望长期沿用的偏好，留在这里。") +
      "</div>";
}
function editNote(id = null) {
  noteId = id;
  const n = state.notes.find((n) => n.id === id);
  $("#note-title").textContent = id ? "编辑记忆" : "添加记忆";
  $("#note-content").value = n?.content || "";
  $("#note-scope").textContent = "个人记忆共享，项目记忆按项目使用。";
  $("#note-range").value = n?.scope || "personal";
  $("#note-project").innerHTML = projectOptions(n?.project_id || state.item.project_id);
  updateNoteScope();
  $("#note-dialog").showModal();
}
$("#note-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/notes", {
      item: itemId,
      id: noteId,
      revision: state.notes.find((n) => n.id === noteId)?.revision,
      scope: $("#note-range").value,
      project_id: Number($("#note-project").value) || null,
      content: $("#note-content").value,
    });
    $("#note-dialog").close();
    await load();
    toast("记忆已保存");
  } catch (error) {
    toast(error.message);
  }
};
$("#drawer-content").onclick = async (e) => {
  const task = e.target.closest("[data-open-task]");
  if (task) { closeDrawer(); return load(task.dataset.openTask); }
  const learningButton = e.target.closest("[data-learning]");
  if (learningButton) {
    const evidence = state.learning.flatMap((l) => l.evidence).find((r) => r.id === Number(learningButton.dataset.learning));
    if (learningButton.dataset.action === "correct") {
      learningId = evidence.id;
      $("#learning-technology").value = evidence.technology;
      $("#learning-method").value = evidence.method;
      $("#learning-evidence").value = "";
      $("#learning-dialog").showModal();
      return;
    }
    if (!confirm("撤销这条学习证据？掌握情况将按剩余证据重新计算。")) return;
    try { await api("/api/learning", {id: evidence.id, action: "revoke"}); await load(); }
    catch (error) { toast(error.message); }
    return;
  }
  const b = e.target.closest("[data-note]");
  if (!b) return;
  const note = state.notes.find((n) => n.id === b.dataset.note);
  if (b.dataset.action === "edit") return editNote(note.id);
  if (
    b.dataset.action === "delete" &&
    !confirm("遗忘这条记忆？后续对话会跳过变更前的上下文；已有聊天仍保留。")
  )
    return;
  try {
    await api("/api/notes", {
      item: itemId,
      id: note.id,
      revision: note.revision,
      status:
        b.dataset.action === "delete"
          ? "deleted"
          : note.status === "active"
            ? "disabled"
            : "active",
    });
    await load();
  } catch (error) {
    toast(error.message);
  }
};
$("#messages").onclick = async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.receiptItem) return load(b.dataset.receiptItem);
  const message = state.messages.find((m) => String(m.id) === b.dataset.id);
  if (b.classList.contains("copy-answer")) {
    try {
      await navigator.clipboard.writeText(message.content);
      const original = b.innerHTML;
      b.innerHTML = `${icon("check")}<span>已复制</span>`;
      setTimeout(() => { if (b.isConnected) b.innerHTML = original; }, 1600);
      toast("已复制回答");
    } catch {
      toast("浏览器未允许复制，请手动选择文字");
    }
  }
  if (b.classList.contains("inspect-answer")) {
    openDrawer("context");
    sourceMessage = b.dataset.id;
    renderDrawer();
  }
};
$("#context").onclick = () => openDrawer("context");
$("#memory").onclick = () => openDrawer("memory");
$("#task-center").onclick = () => openDrawer("tasks");
function updateNoteScope() {
  $("#note-project").hidden = $("#note-project-label").hidden = $("#note-range").value !== "project";
}
$("#note-range").onchange = updateNoteScope;
$("#project-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const project = await api("/api/projects", {name: $("#project-name").value, description: $("#project-description").value});
    await api("/api/item", {item: itemId, project_id: project.id});
    $("#project-dialog").close(); $("#project-form").reset(); await load();
  } catch (error) { toast(error.message); }
};
$("#learning-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await api("/api/learning", {id: learningId, action: "correct", technology: $("#learning-technology").value,
      method: $("#learning-method").value, evidence: $("#learning-evidence").value});
    $("#learning-dialog").close(); await load();
  } catch (error) { toast(error.message); }
};
$("#close-drawer").onclick = closeDrawer;
$("#drawer-backdrop").onclick = closeDrawer;
function closeNavigation(restoreFocus = false) {
  $("#sidebar").classList.remove("open");
  $("#nav-backdrop").hidden = true;
  $("#menu").setAttribute("aria-expanded", "false");
  $("#main").inert = false;
  if (restoreFocus) $("#menu").focus();
}
$("#menu").onclick = () => {
  const open = !$("#sidebar").classList.contains("open");
  if (!open) return closeNavigation(true);
  $("#sidebar").classList.add("open");
  $("#nav-backdrop").hidden = false;
  $("#menu").setAttribute("aria-expanded", "true");
  $("#main").inert = true;
  $("#home").focus();
};
$("#nav-backdrop").onclick = () => closeNavigation(true);
$("#scroll-area").addEventListener("scroll", updateLatest, {passive: true});
$("#scroll-latest").onclick = () => scrollBottom(true);
window.addEventListener("resize", () => {
  if (window.innerWidth > 760) closeNavigation();
  updateLatest();
});
$("#home-shortcut").textContent = /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ 1" : "Ctrl 1";
for (const button of document.querySelectorAll("[data-close]"))
  button.onclick = () => $("#" + button.dataset.close).close();
document.addEventListener("keydown", (e) => {
  // Native dialogs manage their own Escape and focus cycle.
  if (document.querySelector("dialog[open]")) return;
  if (e.key === "Escape") {
    const navOpen = $("#sidebar").classList.contains("open");
    closeDrawer();
    closeNavigation(navOpen);
  }
  if (e.key === "Tab") {
    const scope = drawerKind ? $("#drawer") : $("#sidebar").classList.contains("open") ? $("#sidebar") : null;
    if (scope) {
      const focusable = [...scope.querySelectorAll('button, summary, a[href], input, select, textarea, [tabindex="0"]')]
        .filter((el) => !el.disabled && el.getClientRects().length);
      const first = focusable[0], last = focusable.at(-1);
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
    }
  }
  if ((e.metaKey || e.ctrlKey) && e.key === "1") {
    e.preventDefault();
    load("main");
  }
});
load();
// Status-only polling never calls a model, reloads a transcript or interrupts typing.
setInterval(async () => {
  if (!state || document.hidden) return;
  try {
    const data = await api("/api/tasks");
    if (JSON.stringify(data.items) === JSON.stringify(state.items)) return;
    state.items = data.items;
    renderTasks();
    if (drawerKind === "tasks") renderDrawer();
  } catch {}
}, 3000);
