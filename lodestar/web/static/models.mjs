import { escape as esc } from './markdown.mjs';

export function defaultModel(data) {
  const selection = data.model_selection;
  const explicit = JSON.parse(data.item.settings || '{}').model;
  if (['queued','running','cancelling'].includes(data.run?.status)) return null;
  if (explicit && !selection?.current_blocked) return null;
  const model = selection?.recommended_model;
  return model && model !== data.configuration.model ? model : null;
}

export function modelOptionsHTML(ids, selected, availability = {}) {
  const models = [...new Set(ids)]; // Never put an exhausted current ID back in.
  const placeholder = models.includes(selected) ? '' : '<option value="" selected disabled>请选择模型…</option>';
  return placeholder + models.map((m) => `<option value="${esc(m)}"${m === selected ? ' selected' : ''}>${esc(m)} · ${availability[m] === 'recently_available' ? '最近可用' : '未验证'}</option>`).join('')
    + '<option value="__custom__">自定义模型 ID…</option>';
}

export function hiddenModelsHTML(unavailable = []) {
  if (!unavailable.length) return '';
  return `<summary>已隐藏 ${unavailable.length} 个不可用模型</summary><p class="fineprint">确认额度或配置已恢复后，可重新允许手动尝试；这不会发起推理或证明免费额度。</p>`
    + unavailable.map((m) => `<div class="detail-row"><span>${esc(m.model)} · ${esc(m.reason)}</span><button type="button" class="subtle" data-restore-model="${esc(m.model)}">重新允许尝试</button></div>`).join('');
}
