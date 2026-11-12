import { displayHost, escape as esc, markdown, safeUrl } from "./markdown.mjs";
import { sourceGroups, evidenceHTML } from "./sources.mjs";
import { latestCitedMessage, workspaceMetrics } from "./insights.mjs";
import { actionHTML } from "./actions.mjs";
import { defaultModel, modelOptionsHTML, hiddenModelsHTML } from "./models.mjs";
import { quoteMarkdown } from "./quote.mjs";
import { beginsNewChat, sessionDateLabel, timestampLabel } from "./timeline.mjs";
import { storedFollowups, visibleAnswer } from "./followups.mjs";
import { streamDisplay, scrollFollower, patchStream } from "./stream.mjs";
import { initializeNavigation, initializeChatWidth, initializeAppearance } from "./navigation.mjs";
import { initializeCompanion } from "./companion.mjs";
import { mountDiagrams } from "./diagrams.mjs";
import { impactReference } from "./impact.mjs";
import { waitingHTML, updateWaiting, responseNotice, answerBody, canContinue } from "./response-ui.mjs";
initializeNavigation();
initializeChatWidth();
initializeAppearance();
const companion = initializeCompanion();
let activeStream=null, stoppingRunId=null;
let followScroll=null, streamViewport=null, viewportFrame=null;
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
let memoryFocusId = null,
  selectedAnswerQuote = "";
const REFERENCE_PASTE_THRESHOLD = 600;
let referenceExpanded = false;
let deletingMessageId = null;
let composingPrompt = false;
let finishAfterComposition = null;
try {
  itemId = localStorage.getItem("lodestar-item") || "main";
} catch {}
const initialQuery = new URLSearchParams(location.search);
itemId = initialQuery.get('item') || itemId;
let requestedPaperId = Number(initialQuery.get('paper')) || null;
let requestedMessageId = Number(initialQuery.get('message')) || null;
const labels = {
  model_request: "lolo 正在组织回答",
  get_paper_impact: "正在查论文引用指标",
  search_papers: "正在检索论文",
  discover_papers: "正在发现近期论文",
  search_web: "正在搜索网页",
  search_updates: "正在检索近期动态",
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
const memoryStatusLabels = {active: "已启用", stale: "待核实", disabled: "已停用", archived: "已归档"};
const memoryTierLabels = {session: "短期", durable: "长期"};
const masteryLabels = {unknown: "掌握情况未验证", can_explain: "有讲解证据", can_implement: "有实现证据", can_apply: "有应用证据"};
let learningId = null;
let restoreDraft = true;
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
  companion.status(text, busy);
  if ($("#run-status").textContent !== text) $("#run-status").textContent = text;
  $("#run-status").classList.toggle("busy", busy);
  $("#composer").setAttribute("aria-busy", String(busy));
}
function nearBottom() {
  const el = $("#scroll-area");
  return el.scrollHeight - el.scrollTop - el.clientHeight < 24;
}
function updateLatest() {
  $("#scroll-latest").hidden = !state?.messages.length || (followScroll?.following && nearBottom());
}
function scrollBottom(force = false) {
  if(force) followScroll?.latest();
  else followScroll?.follow();
}
function syncStreamViewport() {
  if(followScroll?.following) {streamViewport?.display.resume();return;}
  if(viewportFrame!==null || !streamViewport) return;
  viewportFrame=requestAnimationFrame(()=>{
    viewportFrame=null;
    if(!streamViewport) return;
    const root=streamViewport.root()?.querySelector('.answer');
    if(!root) return;
    const view=$('#scroll-area').getBoundingClientRect(), tail=root.getBoundingClientRect().bottom;
    if(!followScroll.following && (tail>view.bottom+120 || tail<view.top)) streamViewport.display.pause();
    else streamViewport.display.resume();
  });
}
function readingPosition() {
  const area=$('#scroll-area'), edge=area.getBoundingClientRect().top;
  const message=[...$('#messages').children].find(el=>el.matches('.message') && el.getBoundingClientRect().bottom>edge);
  return {top:area.scrollTop,id:message?.dataset.message,offset:message ? message.getBoundingClientRect().top-edge : 0};
}
function restoreReadingPosition(position) {
  const area=$('#scroll-area');
  const message=position.id && $(`[data-message="${CSS.escape(position.id)}"]`);
  area.scrollTop=message ? area.scrollTop+message.getBoundingClientRect().top-area.getBoundingClientRect().top-position.offset : position.top;
}
function busy() {
  return !!activeRun || submitting;
}
function controls() {
  const inactive = !state || state.item.status !== "active";
  const paused = state && !state.enabled && state.mode !== "mock";
  const unavailable = state?.model_selection?.current_blocked;
  const stopping = activeRun?.status === "cancelling";
  $("#send").disabled = activeRun ? stopping : submitting || inactive || paused || unavailable || !$("#prompt").value.trim();
  if ($("#prompt").disabled !== inactive) $("#prompt").disabled = inactive;
  $("#thinking").disabled = !state;
  $("#model-settings").disabled = !state;
  $("#send").classList.toggle("is-busy", submitting || stopping);
  $("#send use").setAttribute("href", activeRun ? "#i-stop" : "#i-arrow");
  $("#send").setAttribute("aria-label", activeRun ? stopping ? "正在停止" : "停止回答" : "发送问题");
  $("#send").title = activeRun ? stopping ? "收到，正在收住这轮回复" : "停止回答"
    : submitting ? "正在提交"
    : inactive ? "请先恢复任务"
      : paused ? "点击模型名称，启用模型调用"
        : unavailable ? "当前模型不可用，请先选择其他模型"
        : $("#prompt").value.trim() ? "发送问题" : "先输入一个研究问题";
}
function messageTime(message) {
  const label = timestampLabel(message.created_at);
  return label ? `<time class="message-timestamp" datetime="${esc(message.created_at)}">${esc(label)}</time>` : "";
}
function messageReference(message) {
  const text = message.metadata?.reference_text;
  if (typeof text !== "string" || !text.trim()) return "";
  return `<details class="user-reference"><summary>参考文本 · ${text.trim().length} 字</summary><pre>${esc(text.trim())}</pre></details>`;
}
function newChatDivider(message) {
  const date = sessionDateLabel(message.created_at);
  return `<div class="new-chat-divider" role="separator" aria-label="新对话${date ? " · " + esc(date) : ""}"><span></span><strong>新对话</strong>${date ? `<time datetime="${esc(message.created_at)}">${esc(date)}</time>` : ""}<span></span></div>`;
}
function userMessage(message) {
  return `<article class="message message-user" data-message="${esc(message.id)}">${messageReference(message)}<div class="user-bubble">${esc(message.content)}</div><div class="message-actions user-message-actions">${messageTime(message)}<button class="subtle copy-question" data-id="${esc(message.id)}" title="复制这条问题" aria-label="复制这条问题">${icon("copy")}<span>复制问题</span></button>${!busy() ? `<button class="subtle delete-message" data-id="${message.id}" title="删除这一轮问题和回答" aria-label="删除这一轮问题和回答">${icon("trash")}</button>` : ''}</div></article>`;
}
function referenceText() {
  return $("#reference-text").value.trim();
}
function syncReferenceInput() {
  const text = referenceText();
  $("#reference-input").hidden = !referenceExpanded && !text;
  $("#reference-count").textContent = text ? `${text.length} / 24000 字` : "";
  $('#reference-text').hidden = !referenceExpanded;
  $('#reference-preview').hidden = referenceExpanded;
  $('#reference-preview').textContent = text ? text.replace(/^>\s?/gm,'').replace(/\s+/g,' ').slice(0,180) : '添加需要参考的文字';
  $('#toggle-reference').textContent = referenceExpanded ? '收起' : '展开';
  $('#toggle-reference').setAttribute('aria-expanded',String(referenceExpanded));
  $('#toggle-reference').title = referenceExpanded ? '收起编辑区，内容保留并随本次问题发送' : '展开并编辑参考文本';
}
function showReferenceInput() {
  referenceExpanded = true;
  syncReferenceInput();
  $("#reference-text").focus();
}
function appendReference(text) {
  const incoming = String(text || "").trim();
  if (!incoming) return false;
  const field = $("#reference-text"), current = field.value.trim();
  const next = current ? `${current}\n\n${incoming}` : incoming;
  if (next.length > 24000) {
    toast("参考文本最多 24000 字，请删减后再添加");
    return false;
  }
  field.value = next;
  referenceExpanded = false;
  syncReferenceInput();
  field.dispatchEvent(new Event("input", { bubbles: true }));
  return true;
}
function clearReferenceInput() {
  $("#reference-text").value = "";
  referenceExpanded = false;
  syncReferenceInput();
  try { sessionStorage.removeItem('lodestar-reference:' + itemId); } catch {}
}
async function addRequestedPaperReference() {
  const paperId = requestedPaperId;
  requestedPaperId = null;
  if (!paperId) return;
  try {
    const value = await api('/api/paper-library?id=' + encodeURIComponent(paperId));
    const paper = value.paper;
    if (!paper?.title || !paper?.canonical_url) throw new Error('论文资料不完整');
    const metadata = [
      `标题：${paper.title}`,
      `链接：${paper.canonical_url}`,
      paper.authors?.length ? `作者：${paper.authors.join('；')}` : '',
      paper.published_year ? `年份：${paper.published_year}` : '',
      paper.venue ? `发表：${paper.venue}` : '',
      paper.keywords?.length ? `关键词：${paper.keywords.join('；')}` : '',
      value.project?.reading_depth ? `项目阅读深度：${value.project.reading_depth}` : '',
      impactReference(value.impact),
    ].filter(Boolean).join('\n');
    if (appendReference(`论文资料（用户明确引用；以下是元数据，不代表已经阅读全文）\n${metadata}`))
      toast('论文资料已加入上方参考文本；请再输入你的问题');
    const url = new URL(location.href);
    url.searchParams.delete('paper');
    history.replaceState(null, '', url.pathname + (url.search ? url.search : '') + url.hash);
  } catch (error) {
    toast('未能加入论文资料：' + error.message);
  }
}
function renderMessages() {
  $("#messages").innerHTML = state.messages
    .map((m, index) =>
      `${beginsNewChat(state.messages[index - 1], m, itemId) ? newChatDivider(m) : ""}${m.role === "user"
        ? userMessage(m)
        : assistant(
            m.content,
            m.metadata?.model_configuration,
            m.kind === "error",
            m.id,
            m.metadata?.action_receipts,
            m,
          )}`,
    )
    .join("");
  $("#main").classList.toggle("is-empty", !state.messages.length);
  $("#welcome").hidden = !!state.messages.length;
  $("#messages").hidden = !state.messages.length;
  mountDiagrams($("#messages"));
  $("#welcome h1").textContent =
    itemId === "main" ? "今天，想研究什么？" : state.item.name;
  $("#welcome>p").innerHTML =
    itemId === "main"
      ? "发现值得读的论文，理解新方法，<br class=\"mobile-break\">把想法带到正在做的项目中。"
      : "从第一个问题开始，围绕这个课题积累资料、讨论和记忆。";
}
function renderInsights() {
  const metrics = workspaceMetrics(state);
  const cards = [
    ["answers", "研究回答", metrics.answers, "本空间已完成"],
    ["sources", "引用来源", metrics.sources, "可追溯链接"],
    ["context", "当前上下文", metrics.currentContext, "本轮可带入"],
    ["learning", "学习证据", metrics.learningEvidence, "不等于掌握"],
  ];
  const status = metrics.running ? "正在推进" : metrics.activeTasks ? `${metrics.activeTasks} 个任务进行中` : "专注当前对话";
  $("#insight-board").hidden = false;
  $("#insight-board").innerHTML = `<div class="insight-heading"><div><span class="eyebrow">RESEARCH PULSE</span><h2>研究概览</h2></div><span class="insight-status ${metrics.running ? "is-live" : ""}">${esc(status)}</span></div><div class="metric-grid">${cards.map(([action, label, value, note]) => `<button type="button" class="metric-card" data-insight="${action}" title="查看${label}"><span>${label}</span><strong>${value}</strong><small>${note}</small></button>`).join("")}</div><p class="insight-note">这些是活动与来源信号，不是答案质量、结论可信度或掌握程度的评分。</p>`;
}
function assistant(text, config, error = false, id = "draft", receipts, message = {}) {
  const count = id === "draft" ? 0 : sourcesFor(id).references.length;
  let answer = text ? markdown(visibleAnswer(text)) : waitingHTML();
  if (error || message.kind==='error') {
    answer=(message.metadata?.partial_answer ? markdown(message.metadata.partial_answer) : '')+
      responseNotice('error',message.metadata || {},text);
  } else if(message.kind==='cancelled') {
    answer=(message.metadata?.partial_answer ? markdown(message.metadata.partial_answer) : '')+
      responseNotice('cancelled',message.metadata || {});
  }
  const index = state.messages.findIndex((entry) => String(entry.id) === String(id));
  const laterMessages = index < 0 ? [] : state.messages.slice(index + 1);
  const latestAssistant = [...state.messages].reverse().find((entry) => entry.role === "assistant");
  const latestResponse = id !== "draft" && !busy() && index >= 0
    && !laterMessages.some((entry) => entry.role === "user")
    && String(latestAssistant?.id) === String(id);
  const nextSteps = latestResponse && !error ? storedFollowups(message.metadata?.suggested_prompts) : [];
  const followups = nextSteps.length ? `<section class="next-steps" aria-label="推荐的下一步"><span class="next-steps-label">你还可以继续问</span><div class="next-steps-options">${nextSteps.map((step) => `<button type="button" class="next-step" data-id="${esc(id)}" data-next-prompt="${esc(step.prompt)}"><span class="next-step-prompt">${esc(step.prompt)}</span><span class="next-step-arrow">${icon("arrow")}</span></button>`).join("")}</div></section>` : "";
  const retry = latestResponse ? `<button class="subtle retry-answer" data-id="${id}" title="重新生成这条回答">${icon("retry")}<span>重新生成</span></button>` : "";
  const resume = latestResponse && canContinue(message) ? `<button class="subtle continue-answer" data-id="${id}" title="保留已有内容，在这条回答里接着写">${icon("arrow")}<span>继续生成</span></button>` : '';
  const remove = !busy() ? `<button class="subtle delete-message" data-id="${id}" title="删除这条回答">${icon("trash")}<span>删除</span></button>` : '';
  const actions = id === "draft" ? "" : `<div class="message-actions">${messageTime(message)}${resume}${retry}<button class="subtle copy-answer" data-id="${id}" title="复制完整回答">${icon("copy")}<span>复制</span></button><button class="subtle quote-answer" data-id="${id}" title="将整段回答加入参考文本">${icon("paper")}<span>引用整段</span></button><button class="subtle inspect-answer" data-id="${id}" title="查看本轮来源与上下文">${icon("context")}<span>来源与上下文</span>${count ? `<span class="source-count">${count}</span>` : ""}</button>${remove}</div>`;
  return `<article class="message message-assistant ${error ? "message-error" : ""}" data-message="${id}"><div class="assistant-meta"><img class="assistant-avatar" src="/lolo-avatar.png" alt="" width="28" height="28" loading="lazy" decoding="async"><span>lolo</span></div><div class="answer">${answer}</div><div class="execution">${actionHTML(receipts, { pending: id === "draft" })}</div>${followups}${actions}</article>`;
}
function render() {
  $('#memory').href='/memory?item='+encodeURIComponent(itemId);
  $('#projects').href='/projects?item='+encodeURIComponent(itemId);
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
  renderInsights();
  renderMessages();
  if (requestedMessageId) {
    const target = document.querySelector(`[data-message="${CSS.escape(String(requestedMessageId))}"]`);
    if (target) {
      target.scrollIntoView({ block: "center" });
      target.classList.add("is-source-target");
      requestedMessageId = null;
    }
  }
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
    let data = await api("/api/state?item=" + encodeURIComponent(id));
    if (epoch !== generation) return;
    if (defaultModel(data)) {
      const selected = await api('/api/model-default', {item:id});
      if (epoch !== generation) return;
      if (selected.changed) {
        data = await api('/api/state?item=' + encodeURIComponent(id));
        if (epoch !== generation) return;
        toast(`默认使用最近成功模型 ${selected.model}；未自动发送请求`);
      }
    }
    state = data;
    sourceCache = new Map();
    inspectedMessages.clear();
    itemId = id;
    try {
      localStorage.setItem("lodestar-item", id);
    } catch {}
    render();
    if(restoreDraft) {
      restoreDraft=false;
      try {
        const draft=sessionStorage.getItem('lodestar-draft:'+itemId);
        if(draft && !$('#prompt').value) {
          $('#prompt').value=draft.slice(0,24000);
          $('#prompt').dispatchEvent(new Event('input'));
        }
        const reference=sessionStorage.getItem('lodestar-reference:'+itemId);
        if(reference && !referenceText()) {
          $('#reference-text').value=reference.slice(0,24000);
          referenceExpanded=false;
          syncReferenceInput();
        }
      } catch {}
    }
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
    if (!activeRun && state.model_selection?.current_blocked)
      setStatus('当前模型不可用 · 点击模型名称选择其他模型');
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
  const continuing = state.messages.find(m=>Number(m.id)===Number(run.continue_message_id));
  const previousText = continuing ? answerBody(continuing) : '';
  const streamRoot = () => $(`[data-message="${continuing ? continuing.id : 'draft'}"]`);
  const display = streamDisplay((value) => {
    if (epoch !== generation) { stopDisplay(); return; }
    if (composingPrompt) return;
    const el = streamRoot()?.querySelector('.answer');
    if (!el) return;
    patchStream(el,markdown(previousText + value));
    scrollBottom();
    if(!followScroll.following) syncStreamViewport();
  },{interval:()=>followScroll.following ? 48 : 120});
  streamViewport={display,root:streamRoot};
  function stopDisplay() {
    display.stop();
    if(streamViewport?.display===display) streamViewport=null;
  }
  activeStream=display;
  const waitStarted=Date.now();
  let waitPhase='model_request';
  const waitTimer=setInterval(()=>{
    if(epoch!==generation || text) {clearInterval(waitTimer); return;}
    updateWaiting(streamRoot(),waitPhase,(Date.now()-waitStarted)/1000);
  },1000);
  if (continuing) {
    const root=streamRoot();
    root.querySelector('.answer').innerHTML=markdown(previousText)+waitingHTML();
    root.querySelector('.message-actions')?.remove();
    root.querySelector('.next-steps')?.remove();
  } else $("#messages").insertAdjacentHTML(
    "beforeend",
    assistant("", JSON.parse(run.config), false, "draft", []),
  );
  setStatus(run.status === "cancelling" ? "收到，lolo 正在停下…" : "lolo 接到问题啦，稍等一下…", true);
  scrollBottom(true);
  const tick = async () => {
    if (epoch !== generation) { stopDisplay(); clearInterval(waitTimer); return; }
    try {
      const data = await api(
        `/api/events?run=${encodeURIComponent(run.id)}&after=${after}`,
      );
      if (epoch !== generation) { stopDisplay(); return; }
      activeRun = data.run;
      if(stoppingRunId===run.id && ['queued','running'].includes(activeRun.status)) activeRun.status='cancelling';
      controls();
      for (const event of data.events) {
        after = event.id;
        if (event.kind === "text") {
          text += event.data;
          if(stoppingRunId!==run.id) setStatus("lolo 正在写给你…", true);
        } else if (event.kind === "action") {
          receipts.push(event.data);
          const el = streamRoot()?.querySelector('.execution');
          if (el) el.innerHTML = actionHTML(receipts, { pending: true });
        } else if (event.kind === "progress") {
          waitPhase=event.data;
          updateWaiting(streamRoot(),waitPhase,(Date.now()-waitStarted)/1000);
          if(stoppingRunId!==run.id) setStatus((labels[event.data] || "lolo 正在准备资料") + "…", true);
        }
      }
      if(stoppingRunId!==run.id) display.update(visibleAnswer(text));
      if (activeRun.status === "cancelling") setStatus("收到，lolo 正在停下…", true);
      if (!composingPrompt) scrollBottom();
      if (["queued", "running", "cancelling"].includes(data.run.status)) {
        setTimeout(tick, 120);
        return;
      }
      stopDisplay();
      clearInterval(waitTimer);
      if(stoppingRunId===run.id) stoppingRunId=null;
      const result = JSON.parse(data.run.result || "{}");
      const response = await api(
        "/api/state?item=" + encodeURIComponent(itemId),
      );
      if (epoch !== generation) return;
      const follow = followScroll.following;
      const reading = follow ? null : readingPosition();
      activeRun = null;
      if (composingPrompt) {
        setStatus("");
        controls();
        finishAfterComposition = () => { if (epoch === generation) load(itemId); };
        return;
      }
      state = response;
      sourceCache = new Map();
      inspectedMessages.clear();
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
          : data.run.status === "cancelled" ? (text ? "停好啦，已生成的内容给你留着。" : "停好啦，想继续时再叫 lolo。")
          : data.run.status === "interrupted"
            ? "这轮被中断了，可以重新生成。"
            : state.messages.some(m=>canContinue(m)) ? "这条还没写完，可以继续生成，已有内容会保留。" : "这次没接上，可以点重新生成再试一次。",
      );
      if(reading) restoreReadingPosition(reading);
      else scrollBottom();
    } catch (error) {
      stopDisplay();
      clearInterval(waitTimer);
      if (epoch !== generation) return;
      setStatus("和本地服务暂时断开了。后台可能还在运行，刷新后可以接回来。");
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
    stoppingRunId=run.id;
    activeRun.status='cancelling'; activeStream?.stop(); controls();
    updateWaiting($(`[data-message="${run.continue_message_id || 'draft'}"]`),'stopping',0);
    setStatus("收到，lolo 先停一下…",true);
    try {
      const result = await api("/api/cancel", { item: itemId, run: run.id });
      if (epoch !== generation || activeRun?.id !== run.id) return;
      activeRun.status = result.status;
      controls();
      setStatus(result.status === "cancelling" ? "收到，lolo 正在停下…" : "这轮已经收好了。", true);
    } catch (error) { stoppingRunId=null; toast('停止请求没能确认：'+error.message); load(itemId); }
    return;
  }
  const message = $("#prompt").value.trim();
  const reference = referenceText();
  if (!message || busy() || !state || composingPrompt) return;
  const id = itemId,
    epoch = generation;
  submitting = true;
  $("#send").disabled = true;
  setStatus("把问题交给 lolo…", true);
  try {
    const request = await api("/api/send", {
      item: id,
      message,
      reference,
      key: crypto.randomUUID(),
    });
    if (epoch !== generation) return;
    submitting = false;
    $("#prompt").value = "";
    clearReferenceInput();
    try {
      sessionStorage.removeItem('lodestar-draft:'+id);
      sessionStorage.removeItem('lodestar-reference:'+id);
    } catch {}
    $("#prompt").style.height = "auto";
    state.messages.push({ role: "user", content: message,
      metadata: reference ? { reference_text: reference } : {} });
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
async function retryAnswer(messageId) {
  if (!state || busy()) {
    toast("当前回答尚未结束，请稍后再重新生成");
    return;
  }
  const id = itemId;
  const epoch = generation;
  submitting = true;
  setStatus("换个思路，lolo 重新写一版…", true);
  renderMessages();
  try {
    const request = await api("/api/retry", {
      item: id,
      message_id: Number(messageId),
      key: crypto.randomUUID(),
    });
    if (epoch !== generation) return;
    submitting = false;
    state.messages=state.messages.filter(message=>String(message.id)!==String(messageId));
    sourceCache.clear(); inspectedMessages.clear(); renderMessages();
    startPoll({ id: request.id, status: "queued", config: JSON.stringify(state.configuration) });
  } catch (error) {
    if (epoch !== generation) return;
    submitting = false;
    setStatus(error.message);
    renderMessages();
    controls();
    toast(error.message);
  }
}
async function continueAnswer(messageId) {
  if (!state || busy()) return;
  const epoch=generation;
  submitting=true;
  renderMessages(); controls();
  try {
    const request=await api('/api/continue',{item:itemId,message_id:Number(messageId),key:crypto.randomUUID()});
    if(epoch!==generation) return;
    submitting=false;
    startPoll({id:request.id,status:'queued',config:JSON.stringify(state.configuration),continue_message_id:Number(messageId)});
  } catch(error) {
    if(epoch!==generation) return;
    submitting=false;renderMessages();controls();setStatus(error.message);toast(error.message);
  }
}
function requestDeleteMessage(message) {
  if(busy()) {toast('先等 lolo 停下，再删除吧');return;}
  deletingMessageId=message.id;
  $('#delete-message-title').textContent=message.role==='user' ? '删除这一轮对话？' : '删除这条回答？';
  $('#delete-message-description').textContent=(message.role==='user' ? '这条问题和它后面的回答将一起删除。' : '你的问题会保留，只删除这条回答。')+
    '删除后无法撤销，不再进入历史搜索和后续上下文。已保存的记忆、已执行的任务会保留，需要单独管理。';
  $('#delete-message-dialog').showModal();
}
$('#delete-message-form').onsubmit=async(event)=>{
  event.preventDefault();
  if(busy() || deletingMessageId===null) return;
  const epoch=generation, button=event.submitter;
  button.disabled=true;
  try {
    const result=await api('/api/delete-message',{item:itemId,message_id:Number(deletingMessageId)});
    $('#delete-message-dialog').close();
    if(epoch!==generation) return;
    state.messages=state.messages.filter(m=>!result.deleted_ids.includes(m.id));
    sourceCache.clear();inspectedMessages.clear();renderMessages();renderInsights();
    if(drawerKind==='context') closeDrawer();
    setStatus('');toast('已删除，这些内容不会再带入后续对话');
  } catch(error) {toast(error.message);}
  finally {button.disabled=false;deletingMessageId=null;}
};
$("#prompt").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !composingPrompt && event.keyCode !== 229) {
    event.preventDefault();
    if (!busy()) $("#composer").requestSubmit();
  }
});
function resizePrompt() {
  const prompt = $("#prompt");
  const previous = prompt.style.height;
  prompt.style.height = "auto";
  const next = Math.min(prompt.scrollHeight, 190) + "px";
  prompt.style.height = next === previous ? previous : next;
}
$("#prompt").addEventListener("compositionstart", () => { composingPrompt = true; });
$("#prompt").addEventListener("compositionend", () => {
  composingPrompt = false;
  resizePrompt();
  const finish = finishAfterComposition;
  finishAfterComposition = null;
  if (finish) finish();
});
$("#prompt").addEventListener("input", (event) => {
  if (!composingPrompt && !event.isComposing) resizePrompt();
  controls();
});
$("#prompt").addEventListener("paste", (event) => {
  const pasted = event.clipboardData?.getData("text/plain") || "";
  if (pasted.trim().length < REFERENCE_PASTE_THRESHOLD) return;
  event.preventDefault();
  if (appendReference(pasted)) {
    $("#prompt").focus();
    toast("长文本已放到参考文本；请在下方写问题");
  }
});
$("#reference-text").addEventListener("input", () => {
  syncReferenceInput();
});
$("#add-reference").onclick = showReferenceInput;
$("#clear-reference").onclick = () => {clearReferenceInput();$('#prompt').focus();};
$('#toggle-reference').onclick=()=>{
  referenceExpanded=!referenceExpanded;syncReferenceInput();
  if(referenceExpanded) $('#reference-text').focus();
};
$('#reference-preview').onclick=showReferenceInput;
// A full management page must not discard an unsent question when returning.
window.addEventListener('pagehide',()=>{
  try {
    sessionStorage.setItem('lodestar-draft:'+itemId,$('#prompt').value);
    const reference = referenceText();
    if (reference) sessionStorage.setItem('lodestar-reference:'+itemId,reference);
    else sessionStorage.removeItem('lodestar-reference:'+itemId);
  } catch {}
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
let modelCatalog = {}, modelAvailability = {}, settingsGeneration = 0, catalogGeneration = 0;
function selectedModel() {
  return $("#model-select").value === "__custom__"
    ? $("#model-id").value.trim() : $("#model-select").value;
}
function modelOptions(ids, selected) {
  $("#model-select").innerHTML = modelOptionsHTML(ids, selected, modelAvailability);
}

function hiddenModels(selection) {
  const target = $('#hidden-models');
  target.innerHTML = hiddenModelsHTML(selection?.unavailable);
  target.hidden = !selection?.unavailable?.length;
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
  modelAvailability = state.model_selection?.availability || {};
  modelOptions(state.models, state.configuration.model);
  hiddenModels(state.model_selection);
  reasoningOptions(state.reasoning);
  $("#enabled-setting").checked = state.enabled;
  $("#catalog-status").textContent = "正在读取服务端模型目录…";
  $("#settings-dialog").showModal();
  try {
    const catalog = await api("/api/models", { item: currentItem });
    if (revision !== catalogGeneration || currentItem !== itemId || !$("#settings-dialog").open) return;
    modelCatalog = catalog.reasoning;
    modelAvailability = catalog.availability;
    hiddenModels(catalog);
    const selected = selectedModel();
    if ($("#model-select").value !== "__custom__") modelOptions(catalog.models, selected);
    $("#catalog-status").textContent = `${catalog.models.length} 个候选，已隐藏 ${catalog.unavailable.length} 个不可用模型。未验证不代表有免费额度。`;
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
$('#hidden-models').onclick = async (event) => {
  const button = event.target.closest('[data-restore-model]');
  if (!button) return;
  const targetItem = itemId;
  button.disabled = true;
  try {
    await api('/api/model-restore', {item:targetItem, model:button.dataset.restoreModel});
    if (targetItem !== itemId) return;
    const catalog = await api('/api/models', {item:targetItem});
    if (targetItem !== itemId || !$('#settings-dialog').open) return;
    modelAvailability = catalog.availability;
    modelCatalog = catalog.reasoning;
    modelOptions(catalog.models, selectedModel());
    hiddenModels(catalog);
    $('#catalog-status').textContent = '已重新允许手动尝试，未验证额度，也未发起模型请求。';
  } catch (error) { toast(error.message); }
  finally { button.disabled = false; }
};
$("#settings-form").onsubmit = async (e) => {
  e.preventDefault();
  const targetItem = itemId;
  if (!selectedModel()) return toast('请先选择模型');
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
    state.models = updated.models;
    state.model_selection = updated.model_selection;
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
  const key = String(message?.id || "latest");
  if (sourceCache.has(key)) return sourceCache.get(key);
  const sources = sourceGroups(message, state.messages);
  sourceCache.set(key, sources);
  return sources;
}
let sourceMessage = null;
const inspectedMessages = new Set();
let sourceCache = new Map();
let pendingEvidenceUrl = "";
function sourceHTML(source, index = 0) {
  const url = safeUrl(source.url);
  const host = displayHost(url);
  const title = source.title && !/^https?:\/\//i.test(source.title) ? source.title : "打开来源";
  const depth = source.observed === false ? "未关联检索记录" : source.read_depth === "full" ? "正文片段" : source.read_depth === "web" ? "网页片段" : source.read_depth === "abstract" ? "已读摘要" : "检索候选";
  const authority = ({primary:"官方 / 项目", paper:"论文", secondary:"网页报道", signal:"社区线索"})[source.authority] || "";
  return `<article class="source-item"><span class="source-index">${index + 1}</span><div class="source-body">${url ? `<a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="打开外部来源：${esc(host)}"><span>${esc(title)}</span><span class="source-action">打开 ${icon("out")}</span></a>` : esc(source.title || "来源")}<div class="source-host">${esc(host)}</div><div class="source-meta"><span class="source-depth">${depth}</span>${authority ? `<span class="source-origin">${authority}</span>` : ""}<span>${esc(source.date?.slice(0, 10) || "日期未标注")}</span>${source.venue ? `<span>${esc(source.venue)}</span>` : ""}</div>${evidenceHTML(source)}</div></article>`;
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
async function hydrateMessageInspection(messageId) {
  if (!messageId || inspectedMessages.has(String(messageId))) return;
  try {
    const detail = await api(`/api/message-inspection?item=${encodeURIComponent(itemId)}&message=${encodeURIComponent(messageId)}`);
    const full = new Map(detail.messages.map((message) => [String(message.id), message]));
    state.messages = state.messages.map((message) => full.get(String(message.id)) || message);
    inspectedMessages.add(String(messageId));
    sourceCache = new Map();
    if (drawerKind === "context" && String(sourceMessage) === String(messageId)) {
      renderDrawer();
      if (pendingEvidenceUrl) {
        const evidence = [...document.querySelectorAll("[data-evidence-url]")]
          .find((element) => element.dataset.evidenceUrl === pendingEvidenceUrl);
        pendingEvidenceUrl = "";
        if (evidence) {
          evidence.open = true;
          evidence.querySelector("summary")?.focus();
          evidence.scrollIntoView({block: "nearest"});
        }
      }
    }
  } catch (error) {
    toast("未能加载这条回答的来源详情：" + error.message);
  }
}
function openContext(messageId = null, evidenceUrl = "") {
  openDrawer("context");
  sourceMessage = messageId || [...state.messages].reverse().find((message) => message.role === "assistant")?.id || null;
  pendingEvidenceUrl = evidenceUrl;
  renderDrawer();
  void hydrateMessageInspection(sourceMessage);
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
    const loading = message?.metadata?.inspection_available && !inspectedMessages.has(String(message.id));
    target.innerHTML = `<section class="drawer-section"><h3>${message ? "所选回答的范围" : "当前任务范围"}</h3><div class="detail-row"><span>当前任务</span><span>${esc(state.item.name)}</span></div><div class="detail-row"><span>对话</span><span>当前任务 · 变更边界后的近期记录</span></div><div class="detail-row"><span>记忆</span><span>${used ? used.length + " 条本轮带入" : state.context_notes.length + " 条当前可用"}</span></div><div class="detail-row"><span>模型</span><span>${esc(config.model)}</span></div>${web?.history_after ? '<p class="panel-info">本轮已跳过记忆变更前的模型上下文，历史聊天仍可查看。</p>' : ""}<p class="panel-info">同一用户共享个人记忆，同项目共享项目记忆。其他任务的结果可由模型按需读取。</p></section><section class="drawer-section"><h3>项目归属 <button class="subtle" id="register-project">＋ 登记</button></h3><select id="bind-project" aria-label="绑定项目">${projectOptions(state.item.project_id)}</select><p class="fineprint">更换项目会重新建立后续上下文；登记与绑定不会自动读取本地代码。</p></section><section class="drawer-section"><h3>参考来源 <span>${sources.length || ""}</span></h3>${sources.length ? sources.map(sourceHTML).join("") : '<div class="empty-state">这条回答没有引用来源。</div>'}${loading ? '<p class="panel-info">正在加载本轮保存的证据与上下文…</p>' : ''}<p class="panel-info">仅列出这条回答引用的链接；引用不代表内容已经核实。</p></section>${retrieval.length ? `<details class="retrieval-records drawer-section"><summary>检索记录 <span>${retrieval.length}</span></summary><p class="panel-info">本轮检索或阅读过、但未在回答中引用的页面。</p>${retrieval.map(sourceHTML).join("")}</details>` : ""}`;
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
            `<article class="note-item ${n.status === "disabled" || n.status === "archived" ? "disabled" : ""}${n.id === memoryFocusId ? " is-focused" : ""}" data-memory-note="${esc(n.id)}"><p>${esc(n.content)}</p><div class="source-meta"><span>${memoryStatusLabels[n.status] || esc(n.status)}</span><span>${memoryTierLabels[n.tier] || "长期"} · ${esc(n.priority || "P1")}</span><span>${esc(scopeLabels[n.scope])}${n.project_id ? " · " + esc(state.projects.find((p) => p.id === n.project_id)?.name || "") : n.item_id ? " · " + esc(state.items.find((t) => t.id === n.item_id)?.name || "") : ""}</span><span>${n.reference_date ? `资料 ${esc(n.reference_date)}` : `记录 ${esc(String(n.created_at || "").slice(0, 10))}`}</span><span>${n.source === "conversation" ? "来自对话" : n.source === "legacy" ? "旧记录保留" : "手动保存"}</span>${n.source_message_id ? `<span>消息 #${n.source_message_id}</span>` : ""}</div><div class="note-actions"><button class="subtle" data-note="${n.id}" data-action="edit">编辑</button><button class="subtle" data-note="${n.id}" data-action="toggle">${n.status === "active" ? "停用" : "恢复"}</button><button class="subtle" data-note="${n.id}" data-action="delete">遗忘</button></div></article>`,
        )
        .join("")
    : '<div class="empty-state">' +
      (q ? "没有找到匹配的记忆。" : "把希望长期沿用的偏好，留在这里。") +
      "</div>";
  if (memoryFocusId) {
    const focus = document.querySelector(`[data-memory-note="${CSS.escape(memoryFocusId)}"]`);
    if (focus) focus.scrollIntoView({ block: "nearest" });
    memoryFocusId = null;
  }
}
function editNote(id = null) {
  noteId = id;
  const n = state.notes.find((n) => n.id === id);
  $("#note-title").textContent = id ? "编辑记忆" : "添加记忆";
  $("#note-content").value = n?.content || "";
  $("#note-scope").textContent = "个人记忆共享，项目记忆按项目使用。";
  $("#note-range").value = n?.scope || "personal";
  $("#note-tier").value = n?.tier || "durable";
  $("#note-priority").value = n?.priority || "P1";
  $("#note-kind").value = n?.kind || "note";
  $("#note-reference-date").value = n?.reference_date || "";
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
      tier: $("#note-tier").value,
      priority: $("#note-priority").value,
      kind: $("#note-kind").value,
      reference_date: $("#note-reference-date").value || null,
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
  const link = e.target.closest(".answer a");
  if (link) {
    const article = link.closest("[data-message]");
    const source = article && sourcesFor(article.dataset.message).references.find(s=>safeUrl(s.url)===safeUrl(link.href));
    if (source) {
      e.preventDefault(); openContext(article.dataset.message, source.url);
      return;
    }
  }
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.nextPrompt) {
    if (busy()) { toast("当前回答尚未结束，请稍后选择下一步"); return; }
    const prompt = $("#prompt");
    if (prompt.value.trim()) {
      prompt.focus();
      toast("已保留你正在编辑的内容；可手动替换为这条建议");
      return;
    }
    prompt.value = b.dataset.nextPrompt;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    $("#composer").requestSubmit();
    return;
  }
  if (b.classList.contains("retry-answer")) {
    await retryAnswer(b.dataset.id);
    return;
  }
  if (b.classList.contains('continue-answer')) {await continueAnswer(b.dataset.id);return;}
  if (b.dataset.receiptItem) return load(b.dataset.receiptItem);
  if (b.dataset.receiptMemory) {
    memoryFocusId = b.dataset.receiptMemory;
    openDrawer("memory");
    return;
  }
  const message = state.messages.find((m) => String(m.id) === b.dataset.id);
  if (!message) return;
  if (b.classList.contains('delete-message')) {requestDeleteMessage(message);return;}
  if (b.classList.contains("copy-question")) {
    try {
      await navigator.clipboard.writeText(answerBody(message) || message.content);
      toast("已复制问题");
    } catch {
      toast("浏览器未允许复制，请手动选择文字");
    }
    return;
  }
  if (b.classList.contains("copy-answer")) {
    try {
      await navigator.clipboard.writeText(answerBody(message) || message.content);
      const original = b.innerHTML;
      b.innerHTML = `${icon("check")}<span>已复制</span>`;
      setTimeout(() => { if (b.isConnected) b.innerHTML = original; }, 1600);
      toast("已复制回答");
    } catch {
      toast("浏览器未允许复制，请手动选择文字");
    }
    return;
  }
  if (b.classList.contains("quote-answer")) {
    insertQuote(answerBody(message) || message.content);
    return;
  }
  if (b.classList.contains("inspect-answer")) {
    openContext(b.dataset.id);
  }
};
function insertQuote(text) {
  const quote = quoteMarkdown(text);
  if (!quote) return;
  if (!appendReference(quote)) return;
  const prompt = $("#prompt");
  prompt.focus();
  prompt.setSelectionRange(prompt.value.length, prompt.value.length);
  $("#quote-selection").hidden = true;
  selectedAnswerQuote = "";
  toast("已加入上方参考文本");
}
function updateQuoteSelection() {
  const selection = window.getSelection();
  const text = selection?.toString().trim() || "";
  const node = selection?.anchorNode?.nodeType === Node.ELEMENT_NODE ? selection.anchorNode : selection?.anchorNode?.parentElement;
  const answer = node?.closest?.(".message-assistant .answer");
  const button = $("#quote-selection");
  if (!text || !answer || !selection.rangeCount) {
    selectedAnswerQuote = "";
    button.hidden = true;
    return;
  }
  selectedAnswerQuote = text;
  const rect = selection.getRangeAt(0).getBoundingClientRect();
  button.style.left = `${Math.max(10, Math.min(rect.left, window.innerWidth - 156))}px`;
  button.style.top = `${Math.max(10, rect.bottom + 8)}px`;
  button.hidden = false;
}
document.addEventListener("selectionchange", () => requestAnimationFrame(updateQuoteSelection));
$("#quote-selection").onclick = () => insertQuote(selectedAnswerQuote);
$("#context").onclick = () => openContext();
$("#task-center").onclick = () => openDrawer("tasks");
$("#insight-board").onclick = (event) => {
  const action = event.target.closest("[data-insight]")?.dataset.insight;
  if (action === "sources") {
    return openContext(latestCitedMessage(state.messages));
  }
  if (action === "context") return openContext();
  if (action === "learning") return openDrawer("memory");
  if (action === "answers") $("#messages").scrollIntoView({ behavior: "smooth", block: "start" });
};
function updateNoteScope() {
  $("#note-project").hidden = $("#note-project-label").hidden = $("#note-range").value !== "project";
}
$("#note-range").onchange = updateNoteScope;
$("#project-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const project = await api("/api/projects", {name: $("#project-name").value, description: $("#project-description").value,
      allow_model_description:$('#project-description-allow').checked});
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
followScroll=scrollFollower($('#scroll-area'),{onChange:()=>{updateLatest();syncStreamViewport();}});
$('#chat-width').addEventListener('change',()=>{followScroll.follow();syncStreamViewport();});
document.addEventListener('keydown',event=>followScroll.key(event),{capture:true});
new ResizeObserver(()=>{followScroll.follow();syncStreamViewport();}).observe($('#scroll-area'));
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
await load();
await addRequestedPaperReference();
if (initialQuery.has('new-task')) $("#new-task").click();
if (initialQuery.has('tasks')) openDrawer('tasks');
if (initialQuery.has('new-task') || initialQuery.has('tasks')) {
  const url = new URL(location.href);
  url.searchParams.delete('new-task'); url.searchParams.delete('tasks');
  history.replaceState(null, '', url.pathname + url.search);
}
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
