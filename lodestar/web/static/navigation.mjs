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
