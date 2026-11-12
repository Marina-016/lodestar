import { escape as esc, safeUrl } from './markdown.mjs';

const names = {semantic_scholar:'Semantic Scholar',openalex:'OpenAlex'};
const statuses = {not_found:'尚未找到可确认的收录记录',rate_limited:'接口限流，请稍后再试',
  authentication:'接口需要有效凭据',unavailable:'接口暂时不可用',invalid_data:'返回的数据不完整',
  identity_mismatch:'论文标识不匹配，未采用数据',ambiguous:'匹配到多条记录，未采用数据',
  needs_identifier:'缺少可匹配的 DOI 或题名'};
const numeric = (value, max=Infinity) => typeof value === 'number' && Number.isFinite(value) && value>=0 && value<=max;
const count = value => Number.isInteger(value) && value>=0 ? value.toLocaleString('zh-CN') : '未知';
const decimal = value => numeric(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:2}) : '未知';
const percentile = value => !numeric(value,1) ? '未知' : value>.999 && value<1 ? '>99.9%' : (value*100).toFixed(1)+'%';
function when(value) {
  if (!value) return '';
  const date=new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN',{hour12:false});
}
function values(provider, data) {
  return provider==='semantic_scholar' ? [
    ['引用数',count(data.citation_count)],['重要引用（S2 识别）',count(data.influential_citation_count)],
  ] : [
    ['引用数',count(data.citation_count)],['领域归一化影响力 FWCI',decimal(data.fwci)],
    ['归一化引用百分位',percentile(data.citation_percentile)],
  ];
}
export function impactPanel(impact={}) {
  const providers=impact?.providers || {};
  if (impact?.supported===false) return `<section class="detail-section paper-impact"><h3>引用影响力</h3><p class="fineprint">${esc(impact.note)}</p></section>`;
  return `<section class="detail-section paper-impact"><div class="impact-heading"><h3>引用影响力</h3><button class="secondary" data-action="refresh-impact">${Object.keys(providers).length ? '刷新指标' : '查询指标'}</button></div>${impact?.recent ? '<span class="impact-observing">影响力尚待观察 · 新近论文</span>' : ''}<div class="impact-grid">${Object.entries(names).map(([key,name])=>{
    const observation=providers[key] || {},data=observation.data || {};
    const url=safeUrl(data.record_url);
    return `<div class="impact-provider"><strong>${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${name} ↗</a>` : name}</strong><dl>${values(key,data).map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>${data.subfield ? `<p class="fineprint">子领域：${esc(data.subfield)}</p>` : ''}<p class="fineprint">${observation.fetched_at ? '数据查询于 '+esc(when(observation.fetched_at)) : '尚无成功查询记录'}${observation.stale ? ' · 保留上次数据，需更新' : ''}</p>${observation.status && observation.status!=='ok' ? `<p class="impact-status">${esc(statuses[observation.status] || '查询未完成')}${observation.attempted_at ? ' · '+esc(when(observation.attempted_at)) : ''}</p>` : ''}</div>`;
  }).join('')}</div><p class="fineprint">各来源分别计数，缺失值为未知。FWCI 的同年、同类型、同子领域均值为 1；百分位越高表示该组内引用位置越高。引用影响力不等于论文质量或项目适用性。</p><p class="fineprint">点击后才访问学术接口；已查询结果会缓存，连续刷新间隔至少一分钟。</p></section>`;
}

export function impactReference(impact) {
  if (!impact?.providers || !Object.keys(impact.providers).length) return '';
  const lines=['已缓存的引用指标（各来源分开，不能当作质量评分；缺失为未知）：'];
  for (const [key,name] of Object.entries(names)) {
    const row=impact.providers[key]; if(!row) continue;
    lines.push(`${name}：${values(key,row.data || {}).map(([label,value])=>label+' '+value).join('；')}。`+
      `查询时间：${row.fetched_at || '无成功记录'}；最新查询状态：${row.status}${row.stale ? '，旧数据待刷新' : ''}。`);
    const url=safeUrl(row.data?.record_url); if(url) lines.push('指标来源：'+url);
  }
  if(impact.recent) lines.push('新近论文，影响力尚待观察。');
  return lines.join('\n');
}
