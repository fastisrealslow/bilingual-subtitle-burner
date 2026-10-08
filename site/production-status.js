/* Current task projection; history rows are retained in fc_state for audit. */
(function (root) {
  const signatureKeys = ['ts', 'failed', 'published_parts', 'processed_part_indices',
    'source_check_retry_after', 'weekly_full_week'];
  const pendingStatuses = new Set(['running', 'queued', 'retry_queued', 'ready',
    'reserved', 'awaiting_validation', 'awaiting_run']);
  function latestDispatches(state) {
    const latest = new Map();
    for (const entry of state.dispatched || []) {
      if (!entry.slug) continue;
      const current = latest.get(entry.slug);
      if (!current || Number(entry.ts || 0) >= Number(current.ts || 0)) latest.set(entry.slug, entry);
    }
    return [...latest.values()];
  }
  function remaining(entry, state) {
    const pub = (state.published || {})[entry.slug];
    const total = Number((pub || {}).parts_total || entry.parts_total || 0);
    if (pub && total <= 1) return false;
    if (!total) return !pub;
    const done = new Set(entry.processed_part_indices || []);
    for (let i = 0; i < Number(entry.published_parts || 0); i++) done.add(i);
    for (let i = 0; i < total; i++) if (!done.has(i)) return true;
    return false;
  }
  function reconcile(state, snapshot, now = Date.now() / 1000) {
    const fresh = snapshot && now - Number(snapshot.updated_at || 0) < 3 * 3600;
    const bySlug = new Map(((snapshot || {}).tasks || []).map(t => [t.slug, t]));
    const tasks = latestDispatches(state).map(entry => {
      const saved = bySlug.get(entry.slug);
      const matches = fresh && saved && signatureKeys.every(k =>
        JSON.stringify(entry[k] ?? null) === JSON.stringify((saved.signature || {})[k] ?? null));
      let status = 'unknown', detail = '状态尚未核对，不计为生产中';
      if (!remaining(entry, state)) { status = 'complete'; detail = '本批次已处理完毕'; }
      else if (matches) ({status, detail} = saved);
      else if (entry.failed) { status = 'failed'; detail = entry.last_error || '素材验收未通过'; }
      return {...entry, ...(matches ? saved : {}), status, detail};
    });
    return {fresh, tasks, pending: tasks.filter(t => pendingStatuses.has(t.status)),
      history: tasks.filter(t => ['paused', 'obsolete', 'excluded', 'unknown', 'interrupted'].includes(t.status)
        || (['failed', 'validation_failed'].includes(t.status) && now - Number(t.ts || 0) > 7 * 86400)),
      failures: tasks.filter(t => ['failed', 'validation_failed'].includes(t.status))};
  }
  function continuity(snapshot, now = Date.now() / 1000) {
    const data = snapshot && snapshot.continuity;
    const fresh = snapshot && [snapshot.updated_at, snapshot.inventory_updated_at].every(
      ts => Number(ts) > 0 && now - Number(ts) >= -60 && now - Number(ts) < 3 * 3600);
    const stock = data && data.verified_daily_stock;
    const target = data && data.daily_target;
    if (!fresh || !data || !Number.isInteger(stock) || stock < 0
      || !Number.isInteger(target) || target <= 0 || data.quality_gates_relaxed !== false) {
      return {status: 'unknown', message: '每日供应尚未取得有效库存核对，不能保证更新频率。'};
    }
    if (stock === 0) return {status: 'stockout',
      message: `日常合格成片库存为 0，每日 ${target} 条更新目标目前无法保障；生产、候选和周日预留片不计入日常储备。`};
    if (stock < target) return {status: 'at_risk',
      message: `日常合格成片仅 ${stock} 条，低于每日 ${target} 条目标，需要补库。`};
    return {status: 'covered', message: `日常合格库存 ${stock} 条，可覆盖至少一天的 ${target} 条目标；不代表已经发布。`};
  }
  const api = {latestDispatches, remaining, reconcile, continuity};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ProductionStatus = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
