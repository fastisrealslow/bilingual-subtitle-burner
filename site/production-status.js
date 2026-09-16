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
      if (matches) ({status, detail} = saved);
      else if (!remaining(entry, state)) { status = 'complete'; detail = '本批次已处理完毕'; }
      else if (entry.failed) { status = 'failed'; detail = entry.last_error || '素材验收未通过'; }
      return {...entry, ...(matches ? saved : {}), status, detail};
    });
    return {fresh, tasks, pending: tasks.filter(t => pendingStatuses.has(t.status)),
      history: tasks.filter(t => ['paused', 'obsolete', 'excluded', 'unknown', 'interrupted'].includes(t.status)
        || (['failed', 'validation_failed'].includes(t.status) && now - Number(t.ts || 0) > 7 * 86400)),
      failures: tasks.filter(t => ['failed', 'validation_failed'].includes(t.status))};
  }
  const api = {latestDispatches, remaining, reconcile};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ProductionStatus = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
