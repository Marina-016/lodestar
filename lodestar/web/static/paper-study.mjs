import {escape as esc} from './markdown.mjs';
export const readingStates={inbox:'待整理',later:'待学',reading:'在学',read:'已读',archived:'已归档'};
export const masteryStates={unknown:'未评估',aware:'已了解',understand:'能理解',explain:'能讲清',apply:'能应用'};
export const eventNames={explained:'助手讲解',discussed:'参与讨论',attempted:'尝试实践',self_report:'用户自述',demonstrated_explanation:'讲解表现',demonstrated_implementation:'实现表现',demonstrated_application:'应用表现'};
export const evidenceLevels={unknown:'尚无掌握验证',can_explain:'有讲解表现记录',can_implement:'有实现表现记录',can_apply:'有应用表现记录'};
export const stateOptions=(values,selected)=>Object.entries(values).map(([v,label])=>`<option value="${v}"${selected===v?' selected':''}>${label}</option>`).join('');
export function observationText(raw){
 try{const v=JSON.parse(raw);if(v && typeof v==='object' && typeof v.content==='string')return v.content;}catch{}
 return raw;
}
export function studyPanel(value){
 const p=value.paper,s=p.study;if(!s)return '';
 const evidence=value.evidence||[],origins=value.origins||[];
 return `<section class="detail-section paper-study"><form id="paper-study-form"><div class="study-fields"><div><label for="study-status">阅读状态</label><select id="study-status">${stateOptions(readingStates,s.status)}</select></div><div><label for="study-mastery">掌握自评</label><select id="study-mastery">${stateOptions(masteryStates,s.mastery)}</select></div></div><label for="study-note">学习笔记</label><textarea id="study-note" maxlength="3000" rows="3" placeholder="想学什么、哪里还没理解…">${esc(s.note)}</textarea><div class="library-actions"><button type="submit" class="primary">保存学习状态</button><span class="fineprint">${esc(evidenceLevels[p.evidence_mastery]||'尚无掌握验证')} · 自评独立保存</span></div><p id="study-feedback" class="fineprint" role="status"></p></form></section>
 <section class="detail-section"><h3>知识与方法记录</h3>${evidence.length?evidence.map(e=>`<article class="study-evidence"><strong>${esc(e.technology)}${e.method?' · '+esc(e.method):''}</strong><span class="row-meta">${esc(eventNames[e.event]||'学习记录')}</span><p class="memory-body">${esc(observationText(e.evidence))}</p></article>`).join(''):'<p class="fineprint">讨论、实践和讲解表现会显示在这里。</p>'}</section>
 ${origins.some(o=>o.message_id)?`<details class="detail-section"><summary>在对话中出现的位置</summary>${origins.filter(o=>o.message_id).map(o=>`<a class="related-task" href="/?item=${encodeURIComponent(o.item_id)}&amp;message=${Number(o.message_id)}">${esc(o.label||'原始消息')} ↗</a>`).join('')}</details>`:''}`;
}
