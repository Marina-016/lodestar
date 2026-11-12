import test from 'node:test';
import assert from 'node:assert/strict';
import { scrollFollower } from '../lodestar/web/static/stream.mjs';

class Area extends EventTarget {
  scrollHeight=2000;clientHeight=500;top=1500;
  get scrollTop(){return this.top;}
  set scrollTop(value){this.top=Math.max(0,Math.min(value,this.scrollHeight-this.clientHeight));}
  getBoundingClientRect(){return {left:0,right:800};}
  emit(type,values={}) {const event=new Event(type);Object.assign(event,values);this.dispatchEvent(event);}
}
function fixture() {
  const area=new Area(),frames=new Map();let id=0;
  const follow=scrollFollower(area,{schedule:cb=>{frames.set(++id,cb);return id;},cancel:id=>frames.delete(id)});
  return {area,frames,follow,flush(){const batch=[...frames.values()];frames.clear();for(const cb of batch) cb();}};
}
test('upward intent cancels a pending bottom write before native wheel scrolling',()=>{
  const {area,follow,frames,flush}=fixture();
  follow.follow();assert.equal(frames.size,1);
  area.emit('wheel',{deltaY:-20});
  assert.equal(follow.following,false);assert.equal(frames.size,0);
  area.scrollTop-=20;area.emit('scroll');
  // The old 180px heuristic would snap this 20px reading offset to the bottom.
  for(let batch=0;batch<5;batch++) {area.scrollHeight+=100;follow.follow();flush();}
  assert.equal(area.scrollTop,1480);assert.equal(follow.following,false);
});
test('following remains sticky during growth and resumes only at actual bottom or explicit latest',()=>{
  const {area,follow,flush}=fixture();
  area.scrollHeight+=100;follow.follow();flush();
  assert.equal(area.scrollTop,1600);assert.equal(follow.following,true);
  area.emit('wheel',{deltaY:-40});area.scrollTop-=40;area.emit('scroll');
  area.scrollTop=area.scrollHeight-area.clientHeight-10;area.emit('scroll');
  assert.equal(follow.following,false);
  area.scrollTop=area.scrollHeight-area.clientHeight;area.emit('scroll');
  assert.equal(follow.following,true);
  area.emit('wheel',{deltaY:-30});area.scrollTop-=30;area.emit('scroll');
  follow.latest();assert.equal(follow.following,true);assert.equal(area.scrollTop,1600);
});
test('touch, scrollbar and keyboard release following without stealing input field keys',()=>{
  const {area,follow}=fixture();
  follow.key({key:'PageUp',target:{tagName:'TEXTAREA'}});assert.equal(follow.following,true);
  follow.key({key:'PageUp',target:{tagName:'DIV'}});assert.equal(follow.following,false);
  follow.latest();area.emit('touchstart',{touches:[{clientY:100}]});
  area.emit('touchmove',{touches:[{clientY:120}]});
  area.scrollTop-=30;area.emit('scroll');area.emit('touchend');
  assert.equal(follow.following,false);
  follow.latest();area.emit('pointerdown',{clientX:795,pointerType:'mouse'});
  assert.equal(follow.following,false);
});
test('destroy cancels scheduled writes and detaches gesture listeners',()=>{
  const {area,follow,frames}=fixture();
  follow.follow();follow.destroy();assert.equal(frames.size,0);
  area.emit('wheel',{deltaY:-20});assert.equal(follow.following,true);
});
test('a waiting card shrinking at the bottom does not masquerade as an upward gesture',()=>{
  const {area,follow,flush}=fixture();
  area.scrollHeight-=80;area.scrollTop=area.scrollHeight;area.emit('scroll');
  assert.equal(follow.following,true);
  area.scrollHeight+=300;follow.follow();flush();
  assert.equal(area.scrollTop,1720);
});
