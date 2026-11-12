// Spread network batches across frames, with bounded catch-up and one render per
// frame. Array.from keeps emoji/surrogate pairs intact.
export function streamDisplay(render, {schedule = requestAnimationFrame, cancel = cancelAnimationFrame,
  interval=()=>48} = {}) {
  let target = [], latestText='', dirty=false, shown = 0, frame = null, last = 0, stopped = false, paused = false;
  function tick(now) {
    frame = null;
    if (stopped || paused) return;
    if (now - last >= interval()) {
      shown = Math.min(target.length, shown + Math.max(2, Math.ceil((target.length - shown) / 4)));
      render(target.slice(0, shown).join(''));
      last = now;
    }
    if (shown < target.length) frame = schedule(tick);
  }
  return {
    update(text) {
      if(text===latestText) return;
      latestText=text;
      if(paused) {dirty=true;return;}
      target = Array.from(text);
      dirty=false;
      shown = Math.min(shown, target.length);
      if (!stopped && !paused && frame === null && shown < target.length) frame = schedule(tick);
    },
    pause() {
      paused=true;
      if(frame!==null) cancel(frame);
      frame=null;
    },
    resume() {
      if(stopped || !paused) return;
      paused=false;
      if(dirty) {target=Array.from(latestText);dirty=false;}
      // The reader returns to the current stream, not a replay of unseen text.
      shown=target.length;
      if(target.length) render(target.join(''));
    },
    stop() {
      stopped = true;
      if (frame !== null) cancel(frame);
      frame = null;
    },
  };
}

// Following is user intent, not proximity to a growing reply. Never consume the
// native wheel/touch events; release following before the browser scrolls.
export function scrollFollower(area, {onChange=()=>{}, schedule=requestAnimationFrame,
  cancel=cancelAnimationFrame, bottomTolerance=3}={}) {
  let following=true, frame=null, lastTop=area.scrollTop, away=false, touchY=null;
  const atBottom=()=>area.scrollHeight-area.scrollTop-area.clientHeight<=bottomTolerance;
  const notify=()=>onChange(following);
  function pause() {
    following=false;
    away ||= !atBottom();
    if(frame!==null) cancel(frame);
    frame=null;notify();
  }
  function writeBottom() {
    area.scrollTop=area.scrollHeight;
    lastTop=area.scrollTop;away=false;notify();
  }
  function follow() {
    if(!following || frame!==null) return;
    frame=schedule(()=>{frame=null;if(following) writeBottom();});
  }
  function latest() {
    following=true;
    if(frame!==null) cancel(frame);
    frame=null;writeBottom();
  }
  function scroll() {
    const top=area.scrollTop;
    // Collapsing a waiting/error card can clamp scrollTop while still at the
    // bottom. That layout change is not an upward reading gesture.
    if(top<lastTop-1 && !atBottom()) pause();
    if(!atBottom()) away=true;
    else if(away && top>=lastTop && touchY===null) {following=true;away=false;}
    lastTop=top;notify();
  }
  function wheel(event) {
    if(event.deltaY<0) pause();
    else if(event.deltaY>0 && atBottom()) {following=true;notify();}
  }
  function touchStart(event) {touchY=event.touches?.[0]?.clientY ?? null;}
  function touchMove(event) {
    const y=event.touches?.[0]?.clientY;
    if(y!==undefined && touchY!==null && Math.abs(y-touchY)>1) pause();
    touchY=y ?? touchY;
  }
  function touchEnd() {touchY=null;scroll();}
  function key(event) {
    const target=event.target;
    if(target?.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target?.tagName || '')) return;
    if(['ArrowUp','PageUp','Home'].includes(event.key) || (event.key===' ' && event.shiftKey)) pause();
    else if(['End','PageDown','ArrowDown'].includes(event.key) && atBottom()) {following=true;notify();}
  }
  function pointer(event) {
    if(event.pointerType==='touch') return;
    const rect=area.getBoundingClientRect();
    if(event.clientX>=rect.right-18 || event.clientX<=rect.left+18) pause();
  }
  const events={scroll,wheel,touchstart:touchStart,touchmove:touchMove,touchend:touchEnd,touchcancel:touchEnd,pointerdown:pointer};
  for(const [name,handler] of Object.entries(events)) area.addEventListener(name,handler,{passive:true});
  return {get following(){return following;},atBottom,pause,follow,latest,key,
    destroy(){if(frame!==null) cancel(frame);for(const [name,handler] of Object.entries(events)) area.removeEventListener(name,handler);}};
}

// Reconcile safe renderer HTML in place: completed paragraphs, quotes and code
// stay mounted while only changed text/structure is patched. No scroll writes.
export function patchStream(root, html) {
  const template=root.ownerDocument.createElement('template');
  template.innerHTML=html;
  patchChildren(root,template.content);
}
function patchChildren(target, source) {
  let current=target.firstChild;
  for(const next of [...source.childNodes]) {
    if(!current) {target.appendChild(next);continue;}
    const after=current.nextSibling;
    if(current.isEqualNode(next)) {current=after;continue;}
    if(current.nodeType!==next.nodeType || current.nodeName!==next.nodeName) current.replaceWith(next);
    else if(current.nodeType===3 || current.nodeType===8) current.nodeValue=next.nodeValue;
    else {
      for(const attr of [...current.attributes]) if(!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
      for(const attr of [...next.attributes]) if(current.getAttribute(attr.name)!==attr.value) current.setAttribute(attr.name,attr.value);
      patchChildren(current,next);
    }
    current=after;
  }
  while(current) {const after=current.nextSibling;current.remove();current=after;}
}
