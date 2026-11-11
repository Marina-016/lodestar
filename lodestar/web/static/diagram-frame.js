// Runs in an opaque-origin sandbox. It receives text and returns sanitized SVG;
// it never receives the app token, conversation history or access to parent DOM.
const { mermaid, sanitize } = window.LodestarDiagram;
const supported = /^(?:flowchart|graph|mindmap|sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram)\b/;
mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'base', htmlLabels:false,
  maxTextSize:16000, maxEdges:150, suppressErrorRendering:true,
  fontFamily:'Arial, Microsoft YaHei, sans-serif',
  themeVariables:{primaryColor:'#f3f5f8',primaryTextColor:'#20242c',primaryBorderColor:'#c7cdd6',
    lineColor:'#7b8799',secondaryColor:'#edf4ff',tertiaryColor:'#f8f9fb',fontSize:'16px'},
  flowchart:{htmlLabels:false,useMaxWidth:false,curve:'basis',padding:18},
  sequence:{useMaxWidth:false}, mindmap:{useMaxWidth:false},
});
window.addEventListener('message', async event => {
  if (event.source !== parent || event.data?.kind !== 'lodestar-diagram-render') return;
  const {id, source} = event.data;
  if (typeof id !== 'string' || typeof source !== 'string') return;
  try {
    const clean = source.replace(/^\s*%%[^\n]*\n/g, '').trim();
    if (source.length > 16000 || !supported.test(clean) || /%%\s*\{|^---|\bclick\s|@\{|<\s*(?:script|iframe|img|foreignObject)|!\[/im.test(source))
      throw new Error('unsupported');
    const drawing = document.querySelector('#drawing');
    drawing.replaceChildren();
    const {svg} = await mermaid.render('chart-' + id.replace(/[^a-z0-9]/gi,''), source, drawing);
    const doc = new DOMParser().parseFromString(sanitize(svg),'image/svg+xml');
    const root = doc.documentElement;
    if (root.localName !== 'svg') throw new Error('invalid svg');
    for (const el of root.querySelectorAll('*')) {
      for (const attr of [...el.attributes]) {
        if (/^on/i.test(attr.name) || (/href$/i.test(attr.name) && !attr.value.startsWith('#')))
          el.removeAttribute(attr.name);
      }
    }
    const box = root.getAttribute('viewBox')?.split(/[ ,]+/).map(Number);
    if (!box || box.length !== 4 || !box.every(Number.isFinite) || box[2] <= 0 || box[3] <= 0) throw new Error('invalid size');
    root.setAttribute('width',String(box[2])); root.setAttribute('height',String(box[3]));
    root.removeAttribute('style');
    parent.postMessage({kind:'lodestar-diagram-result',id,svg:new XMLSerializer().serializeToString(root),width:box[2],height:box[3]}, '*');
  } catch {
    parent.postMessage({kind:'lodestar-diagram-result',id,error:'这张图暂时未能绘制，请查看图表代码或让助手重新画一次。'}, '*');
  }
});
parent.postMessage({kind:'lodestar-diagram-ready'}, '*');
