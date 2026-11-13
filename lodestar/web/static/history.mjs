import {escape as esc, markdown} from './markdown.mjs';
import {initializeNavigation, revealDetail} from './navigation.mjs';
initializeNavigation();
const $=s=>document.querySelector(s),params=new URLSearchParams(location.search);
let pageNumber=1,epoch=0,detailEpoch=0,selected=null,timer;
const token=$('meta[name="lodestar-token"]').content;
async function api(path){const r=await fetch(path);const v=await r.json();if(!r.ok)throw Error(v.error||'读取失败');return v;}
const stamp=s=>{const d=new Date(typeof s==='string'&&/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)?s.replace(' ','T')+'Z':s);return isNaN(d)?'':d.toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});};
function previewText(raw,query=''){
 const clean=raw.replace(/\[([^]\n]+)\]\(https?:\/\/[^)]+\)/g,'$1').replace(/^\s*#{1,6}\s*|^\s*[-*]\s+/gm,'').replace(/\*\*|`/g,'').replace(/\|\s*[-:]{3,}\s*/g,'').replace(/\|/g,' · ').replace(/\s+/g,' ').trim();
 return query&&!clean.toLocaleLowerCase().includes(query.toLocaleLowerCase())?raw.replace(/\s+/g,' '):clean;
}
function highlight(text,query){
 if(!query)return esc(text);let html='',at=0;const lower=text.toLocaleLowerCase(),needle=query.toLocaleLowerCase();
 for(let next;(next=lower.indexOf(needle,at))!==-1;){html+=esc(text.slice(at,next))+'<mark>'+esc(text.slice(next,next+query.length))+'</mark>';at=next+query.length;}
 return html+esc(text.slice(at));
}
function clearDetail(){++detailEpoch;selected=null;$('#entry-detail').innerHTML='<div class="library-placeholder">选择结果，查看前后对话。</div>';}
function filters(){const p=new URLSearchParams();for(const id of ['query','project','item','role','days']){const v=$('#search-'+id).value.trim();if(v)p.set(id==='item'?'conversation':id,v);}return p;}
async function load(updateURL=true){
 const version=++epoch,p=filters(),query=p.get('query')||'';p.set('page',pageNumber);if(p.has('conversation')){p.set('item',p.get('conversation'));p.delete('conversation');}
 $('#entry-list').setAttribute('aria-busy','true');$('#list-summary').textContent='正在查找…';$('#page-notice').hidden=true;
 const url=filters();if(updateURL)history.replaceState(null,'','/history'+(url.size?'?'+url:''));
 try{const result=await api('/api/conversation-search?'+p);if(version!==epoch)return;
  $('#list-summary').textContent=result.recent?'最近对话 · '+result.total+' 个':result.total+' 条匹配';
  $('#entry-list').innerHTML=result.matches.length?result.matches.map(m=>`<button class="library-row search-hit" data-item="${esc(m.item_id)}" data-message="${m.id}" aria-pressed="false"><span class="search-hit-heading"><strong>${esc(m.item_name)}</strong><time>${stamp(m.created_at)}</time></span><span class="row-text">${highlight(previewText(m.excerpt,query),query)}</span><span class="row-meta">${m.role==='user'?'我':'Lodestar'}${result.recent?' · 继续这段对话':' · 查看上下文'}</span></button>`).join(''):'<div class="library-empty"><strong>没有找到相关对话</strong>试试更短的词，或清除筛选。<button type="button" class="secondary" id="reset-search">清除筛选</button></div>';
  $('#pagination').innerHTML=result.total>20?`<button class="subtle" data-page="${pageNumber-1}" ${pageNumber===1?'disabled':''}>上一页</button><span>${pageNumber} / ${Math.ceil(result.total/20)}</span><button class="subtle" data-page="${pageNumber+1}" ${pageNumber*20>=result.total?'disabled':''}>下一页</button>`:'';
 }catch(e){if(version===epoch){$('#list-summary').textContent='搜索未完成';$('#page-notice').hidden=false;$('#page-notice').textContent=e.message;}}
 finally{if(version===epoch)$('#entry-list').removeAttribute('aria-busy');}
}
async function open(item,id){
 const version=++detailEpoch;selected=id;
 const p=filters();p.set('item',item);p.set('message',id);history.replaceState(null,'','/history?'+p);
 $('#entry-detail').innerHTML='<div class="library-placeholder" role="status">正在读取上下文…</div>';
 for(const row of document.querySelectorAll('.search-hit')){const active=Number(row.dataset.message)===id;row.classList.toggle('selected',active);row.setAttribute('aria-pressed',String(active));}
 try{const result=await api('/api/conversation-context?item='+encodeURIComponent(item)+'&message='+id);if(version!==detailEpoch)return;
  $('#entry-detail').innerHTML=`<div class="search-context-heading"><div><span class="eyebrow">对话上下文</span><h2>${esc(result.item_name)}</h2></div><a class="secondary" href="/?item=${encodeURIComponent(item)}&amp;message=${id}">回到原消息 ↗</a></div>${result.messages.map(m=>{const label=`${m.role==='user'?'我':'Lodestar'} · ${stamp(m.created_at)}`,body=`<div class="markdown">${markdown(m.content)}</div>${m.truncated?'<p class="fineprint">长消息已节选，完整内容见原对话。</p>':''}`;return m.id===id?`<article class="search-context-message target-message" id="target-message"><div class="row-meta"><strong>${esc(label)}</strong><span>匹配消息</span></div>${body}</article>`:`<details class="search-context-message surrounding-message"><summary><span>${esc(label)}</span><span class="context-teaser">${esc(previewText(m.content).slice(0,70))}${m.content.length>70?'…':''}</span></summary>${body}</details>`;}).join('')}`;
  revealDetail();
 }catch(e){if(version===detailEpoch){$('#entry-detail').textContent=e.message;}}
}
function reset(){for(const id of ['query','project','item','role','days'])$('#search-'+id).value='';pageNumber=1;clearDetail();load();}
$('#search-form').onsubmit=e=>{e.preventDefault();pageNumber=1;clearDetail();load();};
for(const id of ['project','item','role','days'])$('#search-'+id).onchange=()=>{pageNumber=1;clearDetail();load();};
$('#clear-filters').onclick=reset;
$('#search-query').addEventListener('search',()=>{if(!$('#search-query').value){pageNumber=1;clearDetail();load();}});
$('#entry-list').onclick=e=>{if(e.target.closest('#reset-search'))return reset();const row=e.target.closest('[data-message]');if(row)open(row.dataset.item,Number(row.dataset.message));};
$('#pagination').onclick=e=>{const b=e.target.closest('[data-page]');if(b){pageNumber=Number(b.dataset.page);clearDetail();load();}};
function closeNav(restore=false){if(restore)$('#menu').focus();$('#sidebar').classList.remove('open');$('#nav-backdrop').hidden=true;$('#main').inert=false;$('#menu').setAttribute('aria-expanded','false');}
$('#menu').onclick=()=>{if($('#sidebar').classList.contains('open'))return closeNav();$('#sidebar').classList.add('open');$('#nav-backdrop').hidden=false;$('#main').inert=true;$('#menu').setAttribute('aria-expanded','true');$('#sidebar a').focus();};
$('#nav-backdrop').onclick=()=>closeNav(true);
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeNav(true);if(e.key==='Tab'&&$('#sidebar').classList.contains('open')){const links=[...$('#sidebar').querySelectorAll('a[href],button:not([disabled])')].filter(e=>e.getClientRects().length),first=links[0],last=links.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
window.addEventListener('resize',()=>{if(innerWidth>760)closeNav();});
try{
 const library=await api('/api/library');
 $('#search-project').innerHTML='<option value="">所有项目</option>'+library.projects.map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('');
 $('#search-item').innerHTML='<option value="">所有对话</option>'+library.items.map(i=>`<option value="${esc(i.id)}">${esc(i.name)}</option>`).join('');
 for(const id of ['query','project','item','role','days'])if(params.get(id==='item'?'conversation':id))$('#search-'+id).value=params.get(id==='item'?'conversation':id);
 const tasks=library.items.filter(i=>i.id!=='main'&&i.status!=='archived');$('#task-count').textContent=tasks.length;$('#tasks-empty').hidden=!!tasks.length;
 $('#task-list').innerHTML=tasks.map(i=>`<a class="nav-item" href="/?item=${encodeURIComponent(i.id)}"><span class="task-name">${esc(i.name)}</span></a>`).join('');
 clearDetail();await load(false);if(params.get('message'))await open(params.get('item')||'main',Number(params.get('message')));
}catch(e){$('#page-notice').hidden=false;$('#page-notice').textContent=e.message;}
