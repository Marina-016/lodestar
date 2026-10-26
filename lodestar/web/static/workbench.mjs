import {escape as esc,safeUrl} from './markdown.mjs';
import {evidenceHTML} from './sources.mjs';
import {stamp} from './library-views.mjs';
const $=s=>document.querySelector(s),token=$('meta[name="lodestar-token"]').content;
const params=new URLSearchParams(location.search),isHistory=location.pathname==='/history';
let projectId=Number(params.get('project'))||0,library,current=null,page=1,total=0,editing=null,epoch=0,pending=false;
async function api(path,data){const r=await fetch(path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-Lodestar-Token':token}:{},body:data?JSON.stringify(data):undefined});const v=await r.json();if(!r.ok)throw Error(v.error||'操作未完成');return v;}
function notice(text){$('#page-notice').textContent=text;$('#page-notice').hidden=!text;}
const actionLabel=s=>({read:'读取论文',assess:'评估适用性',plan:'生成草案',tick:'检查订阅'}[s]||s);
const decisionLabel=s=>({undecided:'尚未决定',investigate:'继续调查',adopt:'决定采用',reject:'不采用'}[s]||s);
const stateLabel=s=>({unread:'待读',read:'已读状态',ignored:'已忽略',queued:'排队中',running:'运行中',completed:'操作结束',error:'未完成',interrupted:'已中断'}[s]||s);
async function load(){
 const version=++epoch;
 try{
  let result;
  if(isHistory){
   const query=$('#search-query').value.trim();if(!query)return;
   const q=new URLSearchParams({query,page:String(page)});if(projectId)q.set('project',projectId);
   result=await api('/api/history?'+q);if(version!==epoch)return;total=result.total;
   $('#entry-list').innerHTML=result.matches.map(m=>`<button class="library-row" data-message="${m.id}" data-item="${esc(m.item_id)}"><strong>${esc(m.item_name)} · ${m.role==='user'?'用户':'助手'} #${m.id}</strong><span class="row-text">${esc(m.excerpt)}</span><span class="row-meta">${stamp(m.created_at)}${m.truncated?' · 匹配片段':''}</span></button>`).join('');
  }else{
   if(!projectId){$('#entry-list').textContent='先在项目页创建或选择项目。';return;}
   result=await api('/api/research?project='+projectId+'&page='+page);if(version!==epoch)return;total=result.total;
   $('#entry-list').innerHTML=result.papers.map(p=>`<button class="library-row" data-paper="${p.id}"><strong>${esc(p.source.title||p.source.url)}</strong><span class="row-meta">${esc(stateLabel(p.state))} · ${p.reading?'有保存的'+(p.reading.read_depth==='full'?'正文片段':'阅读片段'):'未保存阅读证据'}</span></button>`).join('');
   $('#watches').innerHTML=result.watches.map(w=>`<div class="commit-row"><span>${esc(w.query)} · ${w.interval_hours}小时 · ${w.enabled?'已启用':'已暂停'}</span><button class="subtle" data-watch="${w.id}" data-enabled="${!w.enabled}">${w.enabled?'暂停':'恢复'}</button></div>`).join('');
   $('#jobs').innerHTML='<h3>最近操作</h3>'+result.jobs.map(j=>`<details class="revision-entry"><summary>${esc(actionLabel(j.action))} · ${esc(stateLabel(j.status))}</summary><p class="fineprint">${stamp(j.created_at)} · 操作状态不代表语义验收通过</p><pre class="readme-preview">${esc(j.result)}</pre></details>`).join('');
  }
  $('#list-summary').textContent=total+' 条匹配';
  const size=isHistory?20:40;
  $('#pagination').innerHTML=total>size?`<button class="subtle" data-page="${page-1}" ${page===1?'disabled':''}>上一页</button><span>${page}/${Math.ceil(total/size)}</span><button class="subtle" data-page="${page+1}" ${page*size>=total?'disabled':''}>下一页</button>`:'';
 }catch(error){notice(error.message);}
}
async function openMessage(item,id){
 try{const m=await api('/api/history-message?item='+encodeURIComponent(item)+'&message='+id);
 $('#entry-detail').innerHTML=`<h2>${esc(m.item_name)} · 消息 #${m.id}</h2><p class="fineprint">${esc(m.role)} · ${stamp(m.created_at)}${m.truncated?' · 仅显示前24000字符':''}</p><pre class="evidence-text">${esc(m.content)}</pre><a class="text-link" href="/?item=${encodeURIComponent(m.item_id)}">回到所属对话 ↗</a>`;
 history.replaceState(null,'','/history?'+new URLSearchParams({item,message:String(id)}));
 }catch(error){notice(error.message);}
}
async function openPaper(id){
 const expected=projectId;
 try{const d=await api('/api/research-paper?project='+expected+'&paper='+id);if(projectId!==expected)return;current=d;
 const source=d.paper.source,reading=d.reading?.evidence;
 $('#entry-detail').innerHTML=`<h2>${esc(source.title||source.url)}</h2><a class="text-link" href="${esc(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">打开论文原页 ↗</a><div class="library-actions detail-actions"><button class="secondary" data-job="read">读取论文片段</button><button class="subtle" data-state="unread">设为待读</button><button class="subtle" data-state="read">标记已读</button><button class="subtle" data-state="ignored">忽略</button></div>${evidenceHTML({...source,evidence_readings:reading?[{...reading,text:reading.content,source_url:reading.url}]:[]})}<section class="detail-section"><h3>项目适用性与方案</h3><label for="research-goal">本次目标</label><input id="research-goal" maxlength="4000" placeholder="希望在当前项目验证什么？"><p class="fineprint">适用性评估最多1次模型请求；草案最多2次（含一次结构修正）。沿用主对话当前设置，先读取论文并授权项目材料，不自动执行代码或采用。</p><div class="library-actions"><button class="secondary" data-job="assess">评估适用性（调用模型）</button><button class="secondary" data-job="plan">生成方案草案（调用模型）</button><button class="subtle" data-new-plan>手工记录验证方案</button></div></section><section class="detail-section"><h3>方案与实验记录</h3>${d.notes.map(n=>`<article class="revision-entry"><h4>${esc(n.title)}</h4><p class="fineprint">手工记录 · ${esc(decisionLabel(n.decision))} · 修订${n.revision} · 未由本页面执行</p><p class="memory-body">假设：${esc(n.hypothesis)}\n方法：${esc(n.protocol)}\n观察：${esc(n.observation||'尚未记录')}</p><button class="subtle" data-edit-plan="${n.id}">更新记录与决定</button></article>`).join('')}${[['模型草案',d.plans],['实际实验关联记录',d.experiments],['调查记录',d.investigations]].map(([label,rows])=>rows.map(r=>`<details class="revision-entry"><summary>${label} #${r.id} · ${esc(r.status||'已保存')}</summary><p class="fineprint">保留来源与执行范围；不自动代表收益或采用。</p><pre class="readme-preview">${esc(JSON.stringify(r.result,null,2))}</pre></details>`).join('')).join('')}</section>`;
 }catch(error){notice(error.message);}
}
async function mutate(action){if(pending)return;pending=true;try{await action();await load();}catch(error){notice(error.message);}finally{pending=false;}}
async function job(action){
 const id=await api('/api/research-job',{project_id:projectId,paper_id:current?.paper.id,action,goal:$('#research-goal')?.value||'',key:crypto.randomUUID()});
 notice('已提交操作 '+id.id.slice(0,8)+'；未自动重试。点击刷新查看结果。');
}
function editPlan(n=null){editing=n;for(const [key,id] of [['title','name'],['hypothesis','hypothesis'],['protocol','protocol'],['observation','observation'],['decision','decision']])$('#plan-'+id).value=n?.[key]||(key==='decision'?'undecided':'');$('#plan-dialog').showModal();}
$('#entry-list').onclick=e=>{const b=e.target.closest('button');if(b?.dataset.paper)openPaper(Number(b.dataset.paper));if(b?.dataset.message)openMessage(b.dataset.item,Number(b.dataset.message));};
$('#entry-detail').onclick=e=>{const b=e.target.closest('button');if(!b)return;if(b.dataset.job)mutate(()=>job(b.dataset.job));if(b.dataset.state)mutate(async()=>{await api('/api/research-state',{project_id:projectId,paper_id:current.paper.id,state:b.dataset.state});await openPaper(current.paper.id);});if(b.hasAttribute('data-new-plan'))editPlan();if(b.dataset.editPlan)editPlan(current.notes.find(n=>n.id===Number(b.dataset.editPlan)));};
$('#filters').onsubmit=e=>{e.preventDefault();page=1;load();};
$('#project-filter').onchange=()=>{projectId=Number($('#project-filter').value)||0;current=null;page=1;$('#entry-detail').textContent='选择一篇论文或历史消息。';load();};
$('#pagination').onclick=e=>{const b=e.target.closest('[data-page]');if(b){page=Number(b.dataset.page);load();}};
$('#add-paper').onclick=()=>{$('#paper-form').reset();$('#paper-dialog').showModal();};
$('#add-watch').onclick=()=>{$('#watch-form').reset();$('#watch-dialog').showModal();};
$('#refresh').onclick=()=>{loadBackground();load();if(current)openPaper(current.paper.id);};
$('#check-watches').onclick=()=>mutate(()=>job('tick'));
$('#watches').onclick=e=>{const b=e.target.closest('[data-watch]');if(b)mutate(()=>api('/api/research-watch',{project_id:projectId,watch_id:Number(b.dataset.watch),enabled:b.dataset.enabled==='true'}));};
$('#paper-form').onsubmit=e=>{e.preventDefault();mutate(async()=>{const p=await api('/api/research-save',{project_id:projectId,url:$('#paper-url').value,title:$('#paper-name').value});$('#paper-dialog').close();await openPaper(p.id);});};
$('#watch-form').onsubmit=e=>{e.preventDefault();mutate(async()=>{await api('/api/research-watch',{project_id:projectId,query:$('#watch-query').value,terms:$('#watch-terms').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean),interval_hours:Number($('#watch-hours').value)});$('#watch-dialog').close();notice('订阅已保存；未启动后台或模型调用。');});};
$('#plan-form').onsubmit=e=>{e.preventDefault();mutate(async()=>{await api('/api/plan-note',{project_id:projectId,paper_id:current.paper.id,note_id:editing?.id,revision:editing?.revision,title:$('#plan-name').value,hypothesis:$('#plan-hypothesis').value,protocol:$('#plan-protocol').value,observation:$('#plan-observation').value,decision:$('#plan-decision').value});$('#plan-dialog').close();await openPaper(current.paper.id);});};
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$('#'+b.dataset.close).close();
function closeNav(restore=false){if(restore)$('#menu').focus();$('#sidebar').classList.remove('open');$('#nav-backdrop').hidden=true;$('#main').inert=false;$('#menu').setAttribute('aria-expanded','false');}
$('#menu').onclick=()=>{if($('#sidebar').classList.contains('open'))return closeNav();$('#sidebar').classList.add('open');$('#nav-backdrop').hidden=false;$('#main').inert=true;$('#menu').setAttribute('aria-expanded','true');$('#sidebar a').focus();};$('#nav-backdrop').onclick=closeNav;
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.querySelector('dialog[open]'))closeNav(true);if(e.key==='Tab'&&$('#sidebar').classList.contains('open')){const links=[...$('#sidebar').querySelectorAll('a[href]')],first=links[0],last=links.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
$('#research-controls').hidden=isHistory;$('#search-query').hidden=$('#search-button').hidden=isHistory?false:true;
$('#search-query').previousElementSibling.hidden=!isHistory;
$('#page-title').textContent=$('#page-name').textContent=isHistory?'历史检索':'项目研究';
$('#page-description').textContent=isHistory?'找回旧讨论及原消息；助手历史回答仍需核验。':'从论文候选到证据、项目假设和验证记录。';
try{
 library=await api('/api/library');projectId||=library.projects[0]?.id||0;
 if(isHistory&&!params.get('project'))projectId=0;
 $('#project-filter').innerHTML=(isHistory?'<option value="0">所有项目与未绑定对话</option>':'')+library.projects.map(p=>`<option value="${p.id}" ${p.id===projectId?'selected':''}>${esc(p.name)}</option>`).join('');
 $('#task-list').innerHTML=library.items.filter(i=>i.id!=='main'&&i.status!=='archived').map(i=>`<a class="nav-item" href="/?item=${encodeURIComponent(i.id)}">${esc(i.name)}</a>`).join('');$('#task-count').textContent=library.items.length-1;
 await load();if(isHistory&&params.get('message'))await openMessage(params.get('item')||'main',Number(params.get('message')));
}catch(error){notice(error.message);}

async function loadBackground(){
 try{
  const result=await api('/api/background');
  $('#background-panel').innerHTML=`<h3>本地后台检查</h3><label class="check-row"><span><strong>允许后台检查已启用的论文订阅</strong><small>服务运行时约每30秒检查到期项；不调用模型。关闭页面不会停止服务。</small></span><input type="checkbox" id="background-enabled" ${result.enabled?'checked':''}></label><p class="fineprint">${esc(result.scope)} 独立后台启动与可选登录自启，请按开发文档操作；这里不会安装系统任务。</p>`;
  $('#background-enabled').onchange=async e=>{try{await api('/api/background',{enabled:e.target.checked});notice(e.target.checked?'已允许后台检查到期订阅；未调用模型。':'已关闭后续后台检查；已提交操作会正常收尾。');}catch(error){notice(error.message);loadBackground();}};
  $('#notifications').innerHTML=`<h3>本地通知 · ${result.unread} 条未读</h3>`+result.notifications.map(n=>`<div class="commit-row"><span>${esc(n.kind==='task'?'任务':n.kind==='research'?'项目研究':'后台')} · ${esc(n.kind==='task' ? library?.items.find(i=>i.id===n.subject)?.name || n.subject : library?.projects.find(p=>String(p.id)===n.subject)?.name || n.subject)} · ${esc(stateLabel(n.status))} · ${stamp(n.created_at)}</span>${n.seen?'已读':`<button class="subtle" data-notification="${n.id}">标记已读</button>`}</div>`).join('')+(result.notifications.length?'':'<p class="fineprint">没有新通知。无新增论文的成功检查保持安静。</p>');
 }catch(error){notice(error.message);}
}
$('#notifications').onclick=async e=>{const b=e.target.closest('[data-notification]');if(b){try{await api('/api/notification-read',{id:Number(b.dataset.notification)});await loadBackground();}catch(error){notice(error.message);}}};
if(!isHistory)await loadBackground();

$('#background-panel').hidden=$('#notifications').hidden=isHistory;

window.addEventListener('resize',()=>{if(innerWidth>760)closeNav();});
