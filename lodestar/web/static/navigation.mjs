// Shared navigation and desktop layout preference across all workspace pages.
const paths = {
  home: 'M3 10 12 3l9 7v10h-6v-7H9v7H3Z',
  project: 'M3 6h7l2 3h9v12H3Z',
  memory: 'M12 5C9 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-2-1-6-1-9 1Zm0 0v15',
  inbox: 'M4 4h16l2 12v4H2v-4L4 4Zm-2 12h6l2 3h4l2-3h6',
  search: 'M16 16l5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  plus: 'M12 5v14M5 12h14',
  panel: 'M3 4h18v16H3ZM9 4v16M6 9l-2 3 2 3',
  right: 'm9 6 6 6-6 6',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name]}"/></svg>`;

export function initializeNavigation() {
  applyAppearance(readAppearance());
  const sidebar = document.querySelector('#sidebar');
  if (!sidebar) return;
  const page = location.pathname;
  const chat = page === '/';
  const brand = sidebar.querySelector('.brand').outerHTML;
  const account = sidebar.querySelector('.sidebar-bottom').outerHTML;
  const entry = (path, id, label, name, hint) => `<a class="nav-item${page === path ? ' active' : ''}" ${id ? `id="${id}"` : ''} href="${path}" title="${hint}"${page === path ? ' aria-current="page"' : ''}>${icon(name)}<span>${label}</span></a>`;
  sidebar.innerHTML = `<div class="sidebar-heading">${brand}<button type="button" class="icon-button sidebar-collapse" id="sidebar-toggle" title="收起导航栏" aria-label="收起导航栏" aria-controls="sidebar" aria-expanded="true">${icon('panel')}</button></div>
    <nav aria-label="工作空间"><div class="nav-group" aria-label="常用入口">
      ${chat ? `<button class="nav-item active" id="home" aria-current="page">${icon('home')}<span>主对话</span><kbd class="shortcut" id="home-shortcut">Ctrl 1</kbd></button>` : entry('/?item=main', '', '主对话', 'home', '回到主对话')}
      ${entry('/projects', chat ? 'projects' : 'nav-projects', '项目', 'project', '管理项目、工作区与关联研究')}
      ${entry('/memory', chat ? 'memory' : 'nav-memory', '记忆与资料', 'memory', '管理个人记忆、收藏论文和学习记录')}
    </div><div class="nav-group"><div class="nav-group-label">研究工具</div>
      ${entry('/research', '', '研究收件箱', 'inbox', '查看项目里的候选论文和研究结果')}
      ${entry('/history', '', '搜索对话', 'search', '查找以前的聊天消息和来源')}
    </div><div class="nav-group nav-task-group"><div class="nav-section"><span>任务 <span id="task-count" class="section-count">0</span></span>
      ${chat ? '<button type="button" class="nav-all" id="task-center">查看全部</button>' : '<a class="nav-all" href="/?tasks=1">查看全部</a>'}</div>
      ${chat ? `<button class="new-task-button" id="new-task" title="为一个研究课题建立独立任务">${icon('plus')}<span>新建任务</span></button>` : `<a class="new-task-button" href="/?new-task=1">${icon('plus')}<span>新建任务</span></a>`}
      <div id="task-list" class="task-list"></div><p id="tasks-empty" class="sidebar-empty">把需要持续跟进的研究单独建成任务。</p>
      ${chat ? '<button class="subtle archived-toggle" id="archived-toggle" hidden>查看已归档任务</button>' : ''}
    </div></nav>${account}`;
  const reopen = document.createElement('button');
  reopen.type = 'button';
  reopen.id = 'sidebar-reopen';
  reopen.className = 'icon-button sidebar-reopen';
  reopen.title = reopen.ariaLabel = '展开导航栏';
  reopen.setAttribute('aria-controls', 'sidebar');
  reopen.innerHTML = icon('panel');
  document.querySelector('.topbar').prepend(reopen);
  const desktop = matchMedia('(min-width: 761px)');
  let collapsed = false;
  try { collapsed = localStorage.getItem('lodestar-sidebar-collapsed') === 'true'; } catch {}
  function apply() {
    document.documentElement.classList.toggle('sidebar-collapsed', collapsed);
    sidebar.inert = collapsed && desktop.matches;
    sidebar.querySelector('#sidebar-toggle').setAttribute('aria-expanded', String(!collapsed));
    reopen.setAttribute('aria-expanded', String(!collapsed));
  }
  function toggle(value) {
    collapsed = value;
    try { localStorage.setItem('lodestar-sidebar-collapsed', String(value)); } catch {}
    apply();
    (value ? reopen : sidebar.querySelector('#sidebar-toggle')).focus();
  }
  sidebar.querySelector('#sidebar-toggle').onclick = () => toggle(true);
  reopen.onclick = () => toggle(false);
  desktop.addEventListener('change', apply);
  apply();
}

const APPEARANCE_KEY='lodestar-appearance';
const HEX_COLOR=/^#[0-9a-f]{6}$/i;
export function normalizeAppearance(value) {
  const color=v=>typeof v==='string' && HEX_COLOR.test(v) ? v.toLowerCase() : 'default';
  return {accent:color(value?.accent),bubble:color(value?.bubble)};
}
function luminance(color) {
  const values=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16)/255)
    .map(v=>v<=0.04045 ? v/12.92 : ((v+0.055)/1.055)**2.4);
  return values[0]*0.2126+values[1]*0.7152+values[2]*0.0722;
}
export function contrastRatio(a,b) {
  const [dark,light]=[luminance(a),luminance(b)].sort((a,b)=>a-b);
  return (light+0.05)/(dark+0.05);
}
function foreground(background) {
  return contrastRatio(background,'#000000')>contrastRatio(background,'#ffffff') ? '#000000' : '#ffffff';
}
function readableAccent(color) {
  const channels=[1,3,5].map(i=>parseInt(color.slice(i,i+2),16));
  for(let step=0;step<=20;step++) {
    const shade='#'+channels.map(v=>Math.round(v*(1-step/20)).toString(16).padStart(2,'0')).join('');
    if(contrastRatio(shade,'#ffffff')>=4.5) return shade;
  }
  return '#000000';
}
export function appearanceTokens(value) {
  const settings=normalizeAppearance(value),custom=settings.accent!=='default';
  const raw=custom ? settings.accent : '#0071e3',accent=custom ? readableAccent(raw) : raw;
  const bubble=settings.bubble==='default' ? '#f5f5f7' : settings.bubble;
  return {'--accent':accent,'--focus':accent+'26','--focus-border':accent+'66',
    '--accent-line':accent+'30','--accent-selection':accent+'1a','--accent-soft':accent+'0d','--accent-outline':accent+'24',
    '--primary-bg':custom ? raw : '#1d1d1f','--primary-fg':custom ? foreground(raw) : '#ffffff',
    '--primary-hover':custom ? raw : '#3a3a3c','--primary-border':custom ? accent+'40' : 'transparent',
    '--nav-active-bg':custom ? raw+'12' : '#ffffff','--nav-active-ink':custom ? accent : '#1d1d1f',
    '--continue-ink':custom ? accent : '#68558c','--continue-bg':custom ? raw+'16' : '#f2eef8',
    '--source-bg':custom ? raw+'0d' : '#f6f8fb','--source-border':custom ? accent+'24' : '#dfe4ed',
    '--source-hover-bg':custom ? raw+'1a' : '#eef4fa','--source-hover-ink':custom ? accent : '#17497e',
    '--source-host':custom ? accent : '#5c6b7b',
    '--user-bubble-bg':bubble,'--user-bubble-ink':settings.bubble==='default' ? '#1d1d1f' : foreground(bubble),
    '--user-bubble-shadow':settings.bubble==='default' ? 'none' : 'inset 0 0 0 1px #0000000c'};
}
function readAppearance() {
  try {return normalizeAppearance(JSON.parse(localStorage.getItem(APPEARANCE_KEY) || '{}'));}
  catch {return normalizeAppearance({});}
}
function applyAppearance(value) {
  for(const [name,color] of Object.entries(appearanceTokens(value))) document.documentElement.style.setProperty(name,color);
}

export function initializeAppearance() {
  const dialog=document.querySelector('#appearance-dialog'),trigger=document.querySelector('#appearance-settings');
  if(!dialog || !trigger) return;
  let current=readAppearance();
  const presets={
    accent:[['默认','default','#1d1d1f'],['海蓝','#0071e3'],['薄荷','#16836b'],['薰紫','#7764c0'],['暖橙','#c76932'],['玫粉','#bd577d']],
    bubble:[['默认','default','#f5f5f7'],['雾蓝','#e8f0fe'],['浅绿','#e8f4ee'],['淡紫','#eeeafa'],['奶杏','#fff0df'],['浅粉','#fce8ef'],['深海','#253950']],
  };
  for(const [kind,colors] of Object.entries(presets)) {
    const container=dialog.querySelector(`[data-appearance-presets="${kind}"]`);
    container.innerHTML=colors.map(([name,value])=>`<button type="button" class="appearance-swatch" data-appearance-kind="${kind}" data-color="${value}" aria-label="${kind==='accent' ? '重点色' : '气泡颜色'}：${name}" aria-pressed="false"><i aria-hidden="true"></i><span>${name}</span></button>`).join('');
    // Use trusted script properties; this app disallows inline HTML style attributes.
    colors.forEach(([,value,swatch],index)=>{container.children[index].querySelector('i').style.backgroundColor=swatch || value;});
  }
  function update(value,persist=false) {
    current=normalizeAppearance(value);applyAppearance(current);
    for(const kind of ['accent','bubble']) {
      const chosen=current[kind]==='default' ? (kind==='accent' ? '#0071e3' : '#f5f5f7') : current[kind];
      dialog.querySelector(`#appearance-${kind}`).value=chosen;
      dialog.querySelector(`[data-appearance-value="${kind}"]`).textContent=current[kind]==='default' ? '默认' : chosen.toUpperCase();
      for(const button of dialog.querySelectorAll(`[data-appearance-kind="${kind}"]`)) button.setAttribute('aria-pressed',String(button.dataset.color===current[kind]));
    }
    if(persist) {try {localStorage.setItem(APPEARANCE_KEY,JSON.stringify(current));} catch {}}
  }
  dialog.addEventListener('click',event=>{
    const button=event.target.closest('[data-appearance-kind]');
    if(button) update({...current,[button.dataset.appearanceKind]:button.dataset.color},true);
  });
  for(const kind of ['accent','bubble']) dialog.querySelector(`#appearance-${kind}`).addEventListener('input',event=>update({...current,[kind]:event.target.value},true));
  dialog.querySelector('#appearance-reset').onclick=()=>update({},true);
  trigger.onclick=()=>{update(readAppearance());dialog.showModal();};
  window.addEventListener('storage',event=>{
    if(event.key===APPEARANCE_KEY || event.key===null) update(readAppearance());
  });
  update(current);
}
// A browser-local reading preference shared by main chat and task chats.
export function initializeChatWidth() {
  const control=document.querySelector('#chat-width');
  if(!control) return;
  const key='lodestar-chat-width';
  function apply(value) {
    const mode=['compact','standard','wide'].includes(value) ? value : 'standard';
    document.documentElement.dataset.chatWidth=mode;
    control.value=mode;
  }
  let saved='standard';
  try {saved=localStorage.getItem(key) || saved;} catch {}
  apply(saved);
  control.addEventListener('change',()=>{
    apply(control.value);
    try {localStorage.setItem(key,control.value);} catch {}
  });
  window.addEventListener('storage',event=>{
    if(event.key===key) apply(event.newValue);
  });
}
