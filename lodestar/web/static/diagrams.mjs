// Render via an isolated local iframe, display the result as an inert SVG image.
// This keeps model-authored SVG/HTML out of the application's document.
const cache = new Map();
let bridge = null, serial = Promise.resolve();
const dataUrl = svg => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);

function renderer() {
  if (bridge) return bridge;
  bridge = new Promise((resolve, reject) => {
    const frame = document.createElement('iframe');
    frame.className = 'diagram-renderer';
    frame.title = '图表渲染器';
    frame.sandbox = 'allow-scripts';
    frame.setAttribute('aria-hidden','true'); frame.tabIndex = -1;
    const timer = setTimeout(() => { cleanup(); frame.remove(); bridge = null; reject(new Error('图表渲染器加载超时')); }, 15000);
    function cleanup() { clearTimeout(timer); window.removeEventListener('message', ready); }
    function ready(event) {
      if (event.source === frame.contentWindow && event.data?.kind === 'lodestar-diagram-ready') {
        cleanup(); resolve(frame);
      }
    }
    window.addEventListener('message',ready);
    frame.src = '/diagram.html'; document.body.append(frame);
  });
  return bridge;
}

async function render(source) {
  if (cache.has(source)) return cache.get(source);
  const job = serial.then(async () => {
    if (cache.has(source)) return cache.get(source);
    const frame = await renderer();
    return new Promise((resolve,reject) => {
      const id = crypto.randomUUID();
      const timer = setTimeout(() => { cleanup(); frame.remove(); bridge = null; reject(new Error('图表绘制超时，可以让助手简化后重画。')); },12000);
      function cleanup() { clearTimeout(timer); window.removeEventListener('message',done); }
      function done(event) {
        const data = event.data;
        if (event.source !== frame.contentWindow || data?.kind !== 'lodestar-diagram-result' || data.id !== id) return;
        cleanup();
        if (data.error || typeof data.svg !== 'string' || data.svg.length > 2000000 ||
            !Number.isFinite(data.width) || !Number.isFinite(data.height)) {
          reject(new Error(data.error || '图表结果无效')); return;
        }
        const value = {svg:data.svg,width:data.width,height:data.height};
        cache.set(source,value);
        if (cache.size > 20) cache.delete(cache.keys().next().value);
        resolve(value);
      }
      window.addEventListener('message',done);
      frame.contentWindow.postMessage({kind:'lodestar-diagram-render',id,source},'*');
    });
  });
  serial = job.catch(() => {});
  return job;
}

function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = name; document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url),30000);
}
async function png(value) {
  const image = new Image(); image.src = dataUrl(value.svg); await image.decode();
  const scale = Math.min(2,4096/value.width,4096/value.height);
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1,Math.ceil(value.width*scale)); canvas.height = Math.max(1,Math.ceil(value.height*scale));
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff'; ctx.fillRect(0,0,canvas.width,canvas.height);
  ctx.drawImage(image,0,0,canvas.width,canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve,'image/png'));
  if (!blob) throw new Error('图片导出失败，请下载 SVG。');
  download(blob,'lodestar-diagram.png');
}

function expand(value,caption='图表') {
  const dialog = document.createElement('dialog');
  dialog.className = 'diagram-dialog'; dialog.setAttribute('aria-label','放大查看图表');
  dialog.innerHTML = '<header><strong>图表</strong><div><button type="button" data-zoom="out" aria-label="缩小">−</button><output class="diagram-scale" aria-label="图表缩放比例">100%</output><button type="button" data-zoom="fit">适应窗口</button><button type="button" data-zoom="in" aria-label="放大">＋</button><button type="button" data-zoom="close" aria-label="关闭图表">关闭</button></div></header><div class="diagram-viewport" tabindex="0" aria-label="图表画布，可拖动或滚动查看"><img alt="放大的示意图" draggable="false"></div>';
  document.body.append(dialog);
  dialog.querySelector('header strong').textContent=caption;
  const viewport = dialog.querySelector('.diagram-viewport'), image = dialog.querySelector('img');
  image.src = dataUrl(value.svg);
  let scale = 1;
  const resize = () => { image.style.width = Math.max(100,value.width*scale)+'px'; dialog.querySelector('.diagram-scale').textContent=Math.round(scale*100)+'%'; dialog.querySelector('[data-zoom=out]').disabled=scale<=.1; dialog.querySelector('[data-zoom=in]').disabled=scale>=5; };
  const fit = () => { scale = Math.min((viewport.clientWidth-48)/value.width,(viewport.clientHeight-48)/value.height,1); resize(); };
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.onclick = event => {
    const action = event.target.closest('[data-zoom]')?.dataset.zoom;
    if (action === 'close') dialog.close();
    if (action === 'fit') fit();
    if (action === 'in' || action === 'out') { scale = Math.max(.1,Math.min(5,scale*(action==='in'?1.25:.8))); resize(); }
    if (event.target === dialog) dialog.close();
  };
  let drag = null;
  viewport.onpointerdown = event => {
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    drag = {x:event.clientX,y:event.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};
    viewport.setPointerCapture(event.pointerId); viewport.classList.add('is-dragging');
  };
  viewport.onpointermove = event => {
    if (!drag) return;
    viewport.scrollLeft = drag.left + drag.x-event.clientX;
    viewport.scrollTop = drag.top + drag.y-event.clientY;
  };
  viewport.onpointerup = viewport.onpointercancel = () => { drag=null;viewport.classList.remove('is-dragging'); };
  dialog.showModal(); fit();
}

export function mountDiagrams(root) {
  for (const card of root.querySelectorAll('.diagram-card:not([data-mounted])')) {
    card.dataset.mounted = 'true';
    const source = card.querySelector('.diagram-code code').textContent;
    const preview = card.querySelector('.diagram-preview');
    const status = card.querySelector('.diagram-status');
    let value = null;
    status.textContent = '正在绘制图表…';
    card.onclick = async event => {
      const action = event.target.closest('[data-diagram-action]')?.dataset.diagramAction;
      if (!action) return;
      try {
        if (action === 'copy') { await navigator.clipboard.writeText(source); status.textContent='已复制图表代码'; }
        else if (value && action === 'expand') expand(value,card.querySelector('.diagram-toolbar strong').textContent);
        else if (value && action === 'svg') { download(new Blob([value.svg],{type:'image/svg+xml;charset=utf-8'}),'lodestar-diagram.svg'); status.textContent='已导出 SVG'; }
        else if (value && action === 'png') { status.textContent='正在导出…'; await png(value); status.textContent='已导出 PNG'; }
      } catch(error) { status.textContent=error.message || '操作未完成'; }
    };
    render(source).then(result => {
      if (!card.isConnected) return;
      value = result;
      const image = new Image(); image.alt='根据回答内容绘制的示意图';
      image.src=dataUrl(value.svg); image.width=value.width; image.height=value.height;
      preview.replaceChildren(image);
      for (const button of card.querySelectorAll('button[disabled]')) button.disabled=false;
      status.textContent='AI 生成示意图';
    }).catch(error => {
      if (!card.isConnected) return;
      status.textContent='图表未能显示';
      preview.textContent=error.message;
      card.querySelector('.diagram-code').open=true;
    });
  }
}
