import { escape as esc } from './markdown.mjs';

const phases={
  stopping:['收到，lolo 先停一下','正在收住这轮回复'],
  model_request:['等 lolo 捋一捋','正在等模型开始回复'],
  search_papers:['去论文里找找线索','正在检索候选论文'],
  discover_papers:['看看最近的新研究','正在发现候选论文'],
  search_web:['去查查可靠的资料','正在搜索网页'],
  search_updates:['去看看最近有什么变化','正在查询近期信息'],
  read_paper:['找到原文，认真看看','正在读取相关段落'],
  read_webpage:['把资料看仔细一点','正在读取网页内容'],
  get_paper_impact:['看看这篇论文的影响','正在查询引用指标'],
  verify_paper:['再核对一下出处','正在查询发表记录'],
  read_memory:['翻翻我们记下的事','正在读取相关记忆'],
  project_context:['对照一下你的项目','正在读取已授权的项目资料'],
};
export function waitingCopy(phase='model_request', elapsed=0) {
  const [title,detail]=phases[phase] || ['把线索整理好','正在准备回答'];
  return {title,detail:elapsed>=25 ? '这次等得有点久。可以先停止，稍后再试。' :
    elapsed>=12 && phase==='model_request' ? '模型还没开始输出，稍等一下；也可以先停下。' : detail};
}
export function waitingHTML() {
  const copy=waitingCopy();
  return `<div class="response-wait" role="status"><div class="wait-spark" aria-hidden="true"><span>✦</span><i>✧</i><b>·</b></div><div class="wait-copy"><strong data-wait-title>${copy.title}</strong><span data-wait-detail>${copy.detail}</span><div class="wait-lines" aria-hidden="true"><i></i><i></i><i></i></div></div><span class="wait-elapsed" data-wait-elapsed aria-label="已等待">0 秒</span></div>`;
}
export function updateWaiting(root,phase,elapsed) {
  const card=root?.querySelector('.response-wait'); if(!card) return;
  const copy=waitingCopy(phase,elapsed);
  card.querySelector('[data-wait-title]').textContent=copy.title;
  card.querySelector('[data-wait-detail]').textContent=copy.detail;
  card.querySelector('[data-wait-elapsed]').textContent=Math.floor(elapsed)+' 秒';
}
const reasons={timeout:'模型服务没有及时返回完整回复，可以重新生成再试一次。',
  authentication:'模型服务没有放行请求。请检查密钥、权限或服务配置。',
  rate_limit:'模型服务触发了限流，稍等一会儿再试。',
  request_rejected:'请求被模型服务拦住了。可以重新生成，或换一种说法；这不一定是密钥失效。',
  quota_exhausted:'免费额度用完啦。请到模型设置换一个可用模型，不会自动转为付费调用。',
  model_unavailable:'当前模型不可用，请到模型设置换一个可用模型。',
  configuration:'模型与推理配置没有对上，请检查模型设置。',
  connection:'这次没连上模型服务。检查网络或服务地址后再试试。',
  stream_interrupted:'回复的连接中途断开了，已收到的内容会保留。',
  truncated:'已到输出长度上限，回答还没写完。可以点「继续生成」，接着这一条写。'};
export function answerBody(message) {
  return ['error','cancelled'].includes(message?.kind) ? message.metadata?.partial_answer || '' : message?.content || '';
}
export function canContinue(message) {
  return ['error','cancelled'].includes(message?.kind) && Boolean(answerBody(message).trim());
}
export function responseNotice(kind,metadata={},detail='') {
  const stopped=kind==='cancelled';
  const code=metadata.model_error?.code;
  let text=stopped ? (metadata.partial_answer ? '先停在这里，已经写下的内容给你留着～' : '好，这轮先停啦。想继续时，再叫 lolo～') : reasons[code] || metadata.error_message || '这次没能顺利接上。你的问题还在，可以重新生成，让 lolo 再试一次。';
  if(metadata.partial_answer && !/继续生成/.test(text)) text+=' 想接着写，可以点「继续生成」。';
  const title=stopped ? 'lolo 已停下' : metadata.partial_answer ? '这条还没写完' : '这次没接上';
  const diagnostic=metadata.model_error;
  const diagnosticText=diagnostic ? [diagnostic.code,diagnostic.http_status ? 'HTTP '+diagnostic.http_status : ''].filter(Boolean).join(' · ') : detail;
  return `<aside class="response-notice ${stopped ? 'is-stopped' : 'is-error'}"><span class="notice-mark" aria-hidden="true">${stopped ? 'Ⅱ' : '✧'}</span><div><strong>${title}</strong><p>${esc(text)}</p>${!stopped && diagnosticText ? `<details><summary>查看错误详情</summary><span>${esc(diagnosticText)}</span></details>` : ''}</div></aside>`;
}
