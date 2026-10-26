import { escape as esc, safeUrl } from './markdown.mjs';
import { scopes, noteRow, noteDetail, projectRow, projectDetail, stamp } from './library-views.mjs';
const $ = s => document.querySelector(s);
const token = $('meta[name="lodestar-token"]').content;
const isProjects = location.pathname === '/projects';
const params = new URLSearchParams(location.search);
let library, records = [], selected = null, detail = null, history = null;
let mode = '', page = 1, total = 0, epoch = 0, timer, editing = null, learningEditing = null, pending = false;
let contextItem = params.get('item');
try { contextItem ||= localStorage.getItem('lodestar-item'); } catch {}
contextItem ||= 'main';

async function api(path, body) {
  const response = await fetch(path,{method:body ? 'POST' : 'GET',
    headers:body ? {'Content-Type':'application/json','X-Lodestar-Token':token} : {},
    body:body ? JSON.stringify(body) : undefined});
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error || '请求未完成'); error.data = value; throw error; }
  return value;
}
function notice(text) { $('#page-notice').textContent = text; $('#page-notice').hidden = !text; }
function toast(text) {
  $('#toast').textContent = text; $('#toast').hidden = false;
  clearTimeout(timer); timer = setTimeout(()=>$('#toast').hidden=true,4500);
}
const taskName = () => library.items.find(t=>t.id===contextItem)?.name || '主对话';
function options(rows, id, name='name') { return rows.map(r=>`<option value="${esc(String(r.id))}"${r.id===id ? ' selected' : ''}>${esc(r[name])}</option>`).join(''); }
function empty(title, text) { return `<div class="library-empty"><strong>${esc(title)}</strong>${esc(text)}</div>`; }
function renderRows() {
  $('#entry-list').innerHTML = records.length ? records.map(r=> isProjects ? projectRow(r,selected) : mode==='learning' ?
    `<button class="library-row ${selected===r.technology+'|'+r.method ? 'selected' : ''}" data-learning-index="${records.indexOf(r)}"><strong>${esc(r.technology)}</strong><span class="row-text">${esc(r.method || '未指定具体方法')}</span><span class="row-meta">${Number(r.event_count)} 条有效证据 · ${esc(r.mastery==='unknown' ? '掌握情况未验证' : '有历史掌握证据')}</span></button>` : noteRow(r,selected)).join('') :
    isProjects ? empty('让研究有一个项目落点','从 GitHub 导入真实工作区，或先记录一个项目的目标与约束。') :
    mode==='learning' ? empty('还没有匹配的学习证据','从对话中记录你讨论、尝试过的方法。助手读过论文不等于你已掌握。') :
    empty('没有匹配的记忆','添加希望长期沿用的偏好、项目事实或任务备注，也可以更换筛选条件。');
  $('#pagination').innerHTML = !isProjects && mode!=='learning' && total>40 ? `<button class="subtle" data-page="${page-1}"${page===1 ? ' disabled' : ''}>← 上一页</button><span>${page} / ${Math.ceil(total/40)}</span><button class="subtle" data-page="${page+1}"${page*40>=total ? ' disabled' : ''}>下一页 →</button>` : '';
}
function tabs(counts={}) {
  if (isProjects) return;
  $('#library-tabs').innerHTML = [['','全部事实'],['personal','个人'],['project','项目'],['task','任务'],['learning','学习证据']].map(([key,label])=>`<button data-mode="${key}" aria-pressed="${mode===key}">${label}${counts[key] ? ' · '+counts[key] : ''}</button>`).join('');
}
async function refreshLibrary() {
  library = await api('/api/library');
  if (!library.items.some(t=>t.id===contextItem)) contextItem='main';
  $('#return-chat').href = '/?item='+encodeURIComponent(contextItem);
  for (const [id,path] of [['nav-projects','/projects'],['nav-memory','/memory']]) $('#'+id).href=path+'?item='+encodeURIComponent(contextItem);
  const tasks = library.items.filter(t=>t.id!=='main' && t.status!=='archived');
  $('#task-count').textContent = tasks.length;
  $('#task-list').innerHTML = tasks.map(t=>`<a class="nav-item" href="/?item=${encodeURIComponent(t.id)}"><svg><use href="#i-task" /></svg><span class="task-name">${esc(t.name)}</span></a>`).join('');
  $('#tasks-empty').hidden = !!tasks.length;
  const project = $('#project-filter').value || params.get('project') || '';
  $('#project-filter').innerHTML = '<option value="">所有项目</option>'+options(library.projects,Number(project));
}
async function load() {
  const version = ++epoch;
  $('#list-summary').textContent = '正在读取…';
  try {
    if (isProjects) {
      const q = $('#library-search').value.toLocaleLowerCase();
      records = library.projects.filter(p=>(!$('#status-filter').value || p.status===$('#status-filter').value)
        && (p.name+' '+(p.description||'')+' '+(p.repository?.repository||'')).toLocaleLowerCase().includes(q));
      total=records.length; $('#list-summary').textContent=total+' 个项目';
    } else if (mode==='learning') {
      const result = await api('/api/learning-library?query='+encodeURIComponent($('#library-search').value));
      if (version!==epoch) return;
      records=result.profiles; total=result.matching_profiles;
      $('#list-summary').textContent=`${total} 项匹配 · ${result.active_profiles} 项已记录方法${result.profiles_truncated ? ' · 当前展示前 20 项，请搜索其余方法' : ''}`;
    } else {
      const query = new URLSearchParams({query:$('#library-search').value,scope:mode,status:$('#status-filter').value,page:String(page)});
      if ($('#project-filter').value && !['personal','task'].includes(mode)) query.set('project',$('#project-filter').value);
      const result = await api('/api/memory-library?'+query);
      if (version!==epoch) return;
      records=result.notes; total=result.total; tabs(result.counts);
      $('#list-summary').textContent=`${total} 条匹配 · 仅启用的记忆会按范围参与后续对话`;
    }
    renderRows();
  } catch(error) { if(version===epoch) { $('#list-summary').textContent='读取失败'; notice(error.message); } }
}
function clearDetail() { selected=null; detail=null; history=null; $('#entry-detail').innerHTML='<div class="library-placeholder">选择一项，查看内容与来历。</div>'; }
async function openProject(id) {
  selected=Number(id); renderRows();
  const expected=selected;
  try {
    const value=await api('/api/project?id='+selected);
    if(selected!==expected) return;
    detail=value;
    $('#entry-detail').innerHTML=projectDetail(detail,taskName());
  } catch(error) { if(selected===expected) notice(error.message); }
}
async function openNote(id) {
  selected=id; detail=records.find(n=>n.id===id); renderRows();
  try {
    const result=await api('/api/memory-history?id='+encodeURIComponent(id));
    if(selected!==id) return;
    history=result;
    // Never combine a stale row with a newer content history across tabs.
    if(result.revision!==detail.revision) { clearDetail(); await load(); return toast('这条记忆已更新，请重新打开'); }
    $('#entry-detail').innerHTML=noteDetail(detail,result);
  } catch(error) { if(selected===id) notice(error.message); }
}
function openLearning(index) {
  detail=records[Number(index)]; selected=detail.technology+'|'+detail.method; renderRows();
  $('#entry-detail').innerHTML=`<div class="detail-heading"><span class="eyebrow">学习证据 · 不等于自动判卷</span><h2>${esc(detail.technology)}</h2></div><p class="memory-body">${esc(detail.method || '未指定具体方法')}</p><p class="fineprint">${detail.mastery==='unknown' ? '掌握情况未验证' : '掌握情况依据已有历史证据，不代表本轮重新评估'} · ${Number(detail.event_count)} 条有效事件</p>${detail.evidence.map(e=>`<article class="detail-section"><p class="row-meta">${esc(e.event)} · ${stamp(e.created_at)}</p><p class="memory-body">${esc(e.evidence)}</p>${safeUrl(e.paper_url) ? `<a class="text-link" href="${esc(safeUrl(e.paper_url))}" target="_blank" rel="noopener noreferrer">关联论文 ↗</a>` : ''}<div class="library-actions detail-actions"><button class="secondary" data-correct="${e.id}">纠正</button><button class="subtle" data-revoke="${e.id}">撤销证据</button></div></article>`).join('')}`;
}
function owners() {
  const scope=$('#entry-scope').value;
  $('#entry-owner').hidden=$('#entry-owner-label').hidden=scope==='personal';
  $('#entry-owner-label').textContent=scope==='project' ? '所属项目' : '所属任务';
  $('#entry-owner').innerHTML=scope==='project' ? options(library.projects,detail?.project_id) : options(library.items,detail?.item_id || contextItem);
  if(scope==='project' && !library.projects.length) $('#entry-owner').innerHTML='<option value="">请先在项目页创建项目</option>';
  $('#entry-owner').required=scope!=='personal';
}
function editNote(value=null) {
  editing=value;
  $('#entry-title').textContent=value ? '编辑记忆' : '添加记忆';
  $('#entry-content').value=value?.content || '';
  $('#entry-scope').value=value?.scope || ($('#project-filter').value ? 'project' : 'personal');
  owners();
  if(!value && $('#project-filter').value) $('#entry-owner').value=$('#project-filter').value;
  $('#entry-dialog').showModal(); $('#entry-content').focus();
}
function editProject(value=null) {
  editing=value;
  $('#project-editor-title').textContent=value ? '编辑项目' : '新建项目';
  $('#project-name').value=value?.name || ''; $('#project-description').value=value?.description || '';
  $('#project-status').value=value?.status || 'active';
  $('#project-description-allow').checked=value?.allow_model_description || false;
  $('#readme-permission').hidden=!value?.readme;
  $('#project-readme-allow').checked=!!value?.repository?.allow_model_readme;
  $('#project-editor').showModal(); $('#project-name').focus();
}
async function save(form,event,action) {
  event.preventDefault(); if(pending) return;
  pending=true; const controls=[...form.querySelectorAll('button,input,select,textarea')];
  const previous=controls.map(e=>e.disabled); controls.forEach(e=>e.disabled=true);
  form.setAttribute('aria-busy','true');
  try { await action(); } catch(error) {
    notice(error.message); toast(error.message);
    if(error.data?.project_id) { $('#import-dialog').close(); await refreshLibrary(); await load(); await openProject(error.data.project_id); }
  } finally { controls.forEach((e,i)=>e.disabled=previous[i]); form.removeAttribute('aria-busy'); pending=false; }
}
$('#entry-form').onsubmit=e=>save($('#entry-form'),e,async()=>{
  const scope=$('#entry-scope').value;
  await api('/api/notes',{item:contextItem,id:editing?.id,revision:editing?.revision,scope,
    project_id:scope==='project' ? Number($('#entry-owner').value) : null,
    ...(scope==='task' ? {item_id:$('#entry-owner').value} : {}),content:$('#entry-content').value});
  $('#entry-dialog').close(); clearDetail(); await load(); toast('记忆已保存');
});
$('#project-editor-form').onsubmit=e=>save($('#project-editor-form'),e,async()=>{
  const result=await api('/api/project-save',{id:editing?.id,revision:editing?.revision,name:$('#project-name').value,
    description:$('#project-description').value,status:$('#project-status').value,
    allow_model_description:$('#project-description-allow').checked,allow_model_readme:$('#project-readme-allow').checked});
  $('#project-editor').close(); await refreshLibrary(); await load(); await openProject(result.id); toast('项目已保存');
});
$('#import-form').onsubmit=e=>save($('#import-form'),e,async()=>{
  $('#import-status').textContent='正在读取仓库并建立工作区，请等待；不会自动重试。';
  try {
    const result=await api('/api/project-import',{url:$('#repository-url').value,branch:$('#repository-branch').value});
    $('#import-dialog').close(); await refreshLibrary(); await load(); await openProject(result.id); notice('项目与本地 Git 工作区已建立；资料尚未授权给模型。');
  } finally { $('#import-status').textContent=''; }
});
$('#learning-form').onsubmit=e=>save($('#learning-form'),e,async()=>{
  await api('/api/learning',{id:learningEditing,action:'correct',technology:$('#learning-technology').value,
    method:$('#learning-method').value,evidence:$('#learning-evidence').value});
  $('#learning-editor').close(); clearDetail(); await load(); toast('学习证据已纠正');
});
$('#entry-detail').onclick=async event=>{
  const button=event.target.closest('button'); if(!button || pending) return;
  const action=button.dataset.action;
  if(action==='edit-note') return editNote(detail);
  if(action==='edit-project') return editProject(detail);
  if(button.dataset.correct) {
    const evidence=detail.evidence.find(e=>e.id===Number(button.dataset.correct)); learningEditing=evidence.id;
    $('#learning-technology').value=evidence.technology; $('#learning-method').value=evidence.method;
    $('#learning-evidence').value=''; $('#learning-editor').showModal(); return;
  }
  button.disabled=true;
  try {
    if(button.dataset.revoke) {
      if(!confirm('撤销这条学习证据？后续掌握情况将依据剩余证据重新计算。')) return;
      await api('/api/learning',{id:Number(button.dataset.revoke),action:'revoke'});
      clearDetail(); await load(); return;
    }
    if(action==='toggle-note' || action==='forget-note') {
      if(action==='forget-note' && !confirm('确认遗忘这条记忆及其修订正文？已有聊天、备份和已外发信息不会删除。此操作无法在记忆页恢复。')) return;
      await api('/api/notes',{item:contextItem,id:detail.id,revision:detail.revision,status:action==='forget-note' ? 'deleted' : detail.status==='active' ? 'disabled' : 'active'});
      clearDetail(); await load(); toast(action==='forget-note' ? '记忆及其修订正文已遗忘；聊天和备份仍保留' : '记忆状态已更新');
    } else if(action==='bind-project') {
      await api('/api/item',{item:contextItem,project_id:detail.id});
      notice('已将 '+taskName()+' 绑定到 '+detail.name+'；没有自动发送消息。');
    } else if(action==='copy-path') {
      await navigator.clipboard.writeText(detail.workspace.path); toast('已复制工作区路径');
    } else if(action==='refresh-project') await openProject(detail.id);
    else if(action==='sync-project') {
      pending=true; notice('正在安全同步；不会覆盖改动、自动提交或推送。');
      await api('/api/project-import',{id:detail.id,revision:detail.revision});
      const id=detail.id; await refreshLibrary(); await load(); await openProject(id);
      notice('工作区已安全同步。若资料版本变化，README 外发许可已重新关闭，请检查后再授权。');
    }
  } catch(error) { notice(error.message); }
  finally { pending=false; button.disabled=false; }
};
$('#entry-list').onclick=event=>{
  const button=event.target.closest('button'); if(!button) return;
  if(button.dataset.project) openProject(button.dataset.project);
  else if(button.dataset.entry) openNote(button.dataset.entry);
  else if(button.dataset.learningIndex!==undefined) openLearning(button.dataset.learningIndex);
};
$('#pagination').onclick=event=>{ const button=event.target.closest('[data-page]'); if(button) { page=Number(button.dataset.page); clearDetail(); load(); } };
$('#library-tabs').onclick=event=>{
  const button=event.target.closest('[data-mode]'); if(!button) return;
  mode=button.dataset.mode; page=1; clearDetail(); tabs();
  $('#project-filter').hidden=['learning','personal','task'].includes(mode); $('#status-filter').hidden=mode==='learning';
  $('#add-entry').hidden=mode==='learning'; load();
};
let searchTimer;
$('#library-search').oninput=()=>{ clearTimeout(searchTimer); searchTimer=setTimeout(()=>{ page=1; clearDetail(); load(); },250); };
for(const selector of ['#status-filter','#project-filter']) $(selector).onchange=()=>{ page=1; clearDetail(); load(); };
$('#entry-scope').onchange=owners;
$('#add-entry').onclick=()=> isProjects ? editProject() : editNote();
$('#import-project').onclick=()=>{ $('#import-form').reset(); $('#import-dialog').showModal(); $('#repository-url').focus(); };
for(const button of document.querySelectorAll('[data-close]')) button.onclick=()=>$('#'+button.dataset.close).close();
for(const dialog of document.querySelectorAll('dialog')) dialog.addEventListener('cancel',e=>{ if(pending) e.preventDefault(); });
function closeNav(restoreFocus=false) { $('#sidebar').classList.remove('open'); $('#nav-backdrop').hidden=true; $('#menu').setAttribute('aria-expanded','false'); $('#main').inert=false; if(restoreFocus) $('#menu').focus(); }
$('#menu').onclick=()=>{ const open=!$('#sidebar').classList.contains('open'); if(!open) return closeNav(true); $('#sidebar').classList.add('open'); $('#nav-backdrop').hidden=false; $('#menu').setAttribute('aria-expanded','true'); $('#main').inert=true; $('#sidebar a').focus(); };
$('#nav-backdrop').onclick=()=>closeNav(true);
document.addEventListener('keydown',e=>{
  if(e.key==='Escape' && !document.querySelector('dialog[open]')) closeNav(true);
  if(e.key==='Tab' && $('#sidebar').classList.contains('open')) {
    const links=[...$('#sidebar').querySelectorAll('a[href]')],first=links[0],last=links.at(-1);
    if(e.shiftKey && document.activeElement===first) { e.preventDefault(); last.focus(); }
    else if(!e.shiftKey && document.activeElement===last) { e.preventDefault(); first.focus(); }
  }
});
window.addEventListener('resize',()=>{ if(innerWidth>760) closeNav(); });
$('#page-name').textContent=$('#page-title').textContent=isProjects ? '项目' : '记忆';
document.title='Lodestar · '+(isProjects ? '项目' : '记忆');
$('#page-description').textContent=isProjects ? '把正在做的事情留在这里。连接仓库、维护目标，让研究与实际工作相连。' : '把有用的事实慢慢留下来。看清它从哪里来、用于哪里，也随时纠正它。';
$('#add-entry').textContent=isProjects ? '新建项目' : '添加记忆';
$('#import-project').hidden=!isProjects; $('#library-tabs').hidden=isProjects; $('#project-filter').hidden=isProjects;
$('#nav-'+(isProjects ? 'projects' : 'memory')).classList.add('active');
$('#nav-'+(isProjects ? 'projects' : 'memory')).setAttribute('aria-current','page');
$('#status-filter').innerHTML=isProjects ? '<option value="">所有状态</option><option value="active">进行中</option><option value="idea">构想中</option><option value="paused">已暂停</option><option value="archived">已归档</option>' : '<option value="">启用与停用</option><option value="active">已启用</option><option value="disabled">已停用</option>';
$('#library-boundary').textContent=isProjects ? 'Git 工作区与项目资料不是个人记忆。导入不会运行代码或自动授予模型读取权限；仅显式批准的说明与 README 快照可按需外发。项目工作区不包含在 Web 数据库备份中，请单独备份本地未推送提交。' : '记忆长期保存在本地数据库。个人事实跨任务，项目事实限同项目，任务备注只用于指定任务。停用可恢复；遗忘不清除聊天、备份或服务商已接收的信息。学习证据与普通事实分开维护。';
try { await refreshLibrary(); tabs(); await load(); } catch(error) { notice('本地服务未连接：'+error.message); }
