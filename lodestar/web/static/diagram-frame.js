// Runs in an opaque-origin sandbox. It receives text and returns sanitized SVG;
// it never receives the app token, conversation history or access to parent DOM.
const { mermaid, sanitize } = window.LodestarDiagram;
const supported = /^(?:flowchart|graph|mindmap|sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|erDiagram)\b/;
mermaid.initialize({startOnLoad:false, securityLevel:'strict', theme:'base', htmlLabels:false,
  maxTextSize:16000, maxEdges:150, suppressErrorRendering:true,
  look:'classic', fontFamily:'Microsoft YaHei, Arial, sans-serif',
  themeVariables:{background:'#ffffff',primaryColor:'#f0f5ff',primaryTextColor:'#2d405e',primaryBorderColor:'#c5d5ef',
    lineColor:'#98a7bc',secondaryColor:'#f4effc',secondaryTextColor:'#584d70',secondaryBorderColor:'#d8cdea',
    tertiaryColor:'#f7f8fb',tertiaryTextColor:'#536078',tertiaryBorderColor:'#dde3ec',fontSize:'15px',
    clusterBkg:'#fafbfe',clusterBorder:'#e0e6f0',edgeLabelBackground:'#ffffff',
    actorBkg:'#edf3ff',actorBorder:'#cbd8ee',actorTextColor:'#344766',actorLineColor:'#d5deeb',
    signalColor:'#788ba8',signalTextColor:'#4b5f7c',labelBoxBkgColor:'#f5f1fb',labelBoxBorderColor:'#ddd2ed',
    noteBkgColor:'#fff8eb',noteBorderColor:'#efdfbd',noteTextColor:'#796a4c',
    cScale0:'#e8f0fe',cScale1:'#efe8fb',cScale2:'#f8eddf',cScale3:'#e9eef5',
    cScale4:'#e8f0fe',cScale5:'#efe8fb',cScale6:'#f8eddf',cScale7:'#e9eef5',
    cScale8:'#e8f0fe',cScale9:'#efe8fb',cScale10:'#f8eddf',cScale11:'#e9eef5',
    cScaleLabel0:'#344766',cScaleLabel1:'#58496d',cScaleLabel2:'#7b644a',cScaleLabel3:'#4c5d77',
    cScaleLabel4:'#344766',cScaleLabel5:'#58496d',cScaleLabel6:'#7b644a',cScaleLabel7:'#4c5d77',
    cScaleLabel8:'#344766',cScaleLabel9:'#58496d',cScaleLabel10:'#7b644a',cScaleLabel11:'#4c5d77'},
  themeCSS:'.node rect,.actor {rx:10px;ry:10px;filter:none!important;} .node polygon,.node circle,.node path {filter:none!important;} .node .label-container {stroke-width:1.2px;} .edgePath .path,.flowchart-link {stroke-width:1.4px;} .edgeLabel {font-size:12px;} .cluster rect {rx:12px;ry:12px;} .cluster-label text {fill:#657792;font-weight:500;} .mindmap-node text {font-size:14px;} .messageText {font-size:13px;} .actor {font-weight:500;}',
  flowchart:{htmlLabels:false,useMaxWidth:false,curve:'basis',padding:20,nodeSpacing:36,rankSpacing:46,wrappingWidth:230},
  sequence:{useMaxWidth:false,actorMargin:45,messageMargin:35,diagramMarginX:20,diagramMarginY:20},
  mindmap:{useMaxWidth:false,padding:18},
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
