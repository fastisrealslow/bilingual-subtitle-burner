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
      failures: tasks.filter(t => ['failed', 'validation_failed', 'experiment_fallback'].includes(t.status))};
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
      message: `日常合格成片库存为 0，每日 ${target} 条更新目标目前无法保障；${[2,3].includes(data.plan?.version) ? `未来七天目标共 ${7 * target} 条。` : data.plan ? '每天至少 2 条、争取 3 条的底线也尚未达到，未来七天至少缺 '+data.plan.minimum_week_shortfall+' 条。' : ''}生产、候选和周日预留片不计入日常储备。`};
    const experiment = snapshot.publishing_experiment;
    const requirement = experiment && experiment.daily_requirement;
    const backlog = snapshot.backlog;
    const goalRow = row => row && Number.isInteger(row.target) && row.target > 0
      && (row.verified === null || Number.isInteger(row.verified) && row.verified >= 0);
    const backlogWarning = backlog && backlog.in_flight_counted === false && backlog.quality_gates_relaxed === false
      && [backlog.ordinary, backlog.treatment, backlog.seeds].every(goalRow)
      ? ` 扩库目标（不含排队任务）：普通 ${backlog.ordinary.verified ?? '待核对'}/${backlog.ordinary.target}、实验 ${backlog.treatment.verified ?? '待核对'}/${backlog.treatment.target}；独立视频种子 ${backlog.seeds.verified ?? '待核对'}/${backlog.seeds.target}。` : '';
    const dailyWarning = requirement && requirement.active
      && Number.isInteger(requirement.actual_treatment) && requirement.actual_treatment >= 0
      ? ` 今日实验稿 ${requirement.actual_treatment}/2，尚需 ${Math.max(0, 2 - requirement.actual_treatment)} 条；${requirement.stable_7_complete_days ? '最近七个完整日均达到两条回执，公开状态仍以创作中心核验为准。' : '尚未连续七个完整日达到每天两条。'}` : '';
    const experimentWarning = (experiment && experiment.active && experiment.status === 'needs_attention'
      ? ` 实验尚未达标：累计实际实验投稿 ${experiment.actual_treatment_published} 条，有效对比 ${experiment.valid_comparison_days} 天，已连续 ${experiment.consecutive_fallbacks} 个实验档位由常规片补位。已验收实验库存：横屏 ${Number((experiment.verified_treatment || {}).landscape) || 0} 条、竖屏 ${Number((experiment.verified_treatment || {}).portrait) || 0} 条，分别需至少 2 条储备。`
      : experiment && experiment.active && experiment.status !== 'ready'
        ? ' 实验状态尚未确认，不能用普通库存保证每天两条实验稿。' : '') + dailyWarning + backlogWarning;
    const dailyRisk = !!dailyWarning && (requirement.stable_7_complete_days !== true
      || (Number.isInteger(requirement.gap_by_now) ? requirement.gap_by_now > 0 : requirement.actual_treatment < 2));
    // Daily progress remains visible even when healthy. Informational text
    // must not turn a genuinely ready, stable snapshot into a permanent alarm.
    const experimentRisk = experiment && experiment.active && experiment.status !== 'ready';
    const warning = experimentRisk || dailyRisk;
    const plan = data.plan;
    if (plan?.version === 3) {
      const profiles = plan.verified_by_profile;
      const layouts = ['portrait', 'landscape'];
      if (!profiles || !['ordinary', 'experiment'].every(k => profiles[k]
        && layouts.every(l => Number.isInteger(profiles[k][l]) && profiles[k][l] >= 0))
        || plan.daily_by_profile?.ordinary !== 2 || plan.daily_by_profile?.experiment !== 2
        || plan.daily_landscape_stories !== 1 || target !== 4
        || ['ordinary', 'experiment'].reduce((n,k) => n + profiles[k].portrait + profiles[k].landscape, 0) !== stock) {
        return {status: 'unknown', message: '每日供应缺少普通稿与故事稿的有效库存核对，不能保证每天两条故事稿。'};
      }
      const ordinary = profiles.ordinary.portrait + profiles.ordinary.landscape;
      const stories = profiles.experiment.portrait + profiles.experiment.landscape;
      const days = Math.min(Math.floor(ordinary / 2), Math.floor(stories / 2), profiles.experiment.landscape);
      const ordinaryGap = Math.max(0, 14 - ordinary);
      const storyGap = Math.max(0, 14 - stories, 7 - profiles.experiment.landscape);
      const reserveDays = Math.ceil(data.reserve_target / target);
      if (!Number.isInteger(data.reserve_target) || data.reserve_target < 1)
        return {status: 'unknown', message: '每日供应缺少有效储备目标。'};
      const reserveGap = Math.max(0, 2 * reserveDays - ordinary)
        + Math.max(0, 2 * reserveDays - stories, reserveDays - profiles.experiment.landscape);
      const admission = snapshot.source_admission;
      const sourceWarning = stories < 14 && admission?.known_long_candidates === 0
        ? ' 故事类长母片候选为 0，需补充长母片；短片候选不能填补此缺口。' : '';
      return {status: warning || days < 1 || reserveGap ? 'at_risk' : 'covered',
        message: `日常合格库存 ${stock} 条（普通稿 ${ordinary}、故事稿 ${stories}，其中横屏故事 ${profiles.experiment.landscape}）。按每天普通稿 2 条、故事稿 2 条且至少 1 条横屏故事，可覆盖 ${days} 个完整日；未来七天还缺普通稿 ${ordinaryGap}、故事稿 ${storyGap} 条。${reserveGap ? `当前储备目标还缺 ${reserveGap} 条。` : ''}发布前仍需复验，不代表已经发布。${sourceWarning}${experimentWarning}`};
    }
    if (plan?.version === 2) {
      const counts = plan.verified_by_layout, needs = plan.daily_by_layout, reserves = plan.reserve_targets_by_layout;
      const layouts = ['portrait', 'landscape'];
      if (![counts, needs, reserves].every(row => row && layouts.every(k => Number.isInteger(row[k]) && row[k] >= 0))
        || layouts.reduce((n, k) => n + counts[k], 0) !== stock
        || layouts.reduce((n, k) => n + needs[k], 0) !== target) {
        return {status: 'unknown', message: '每日供应缺少有效的横竖屏库存核对，不能按总数保证更新频率。'};
      }
      const days = Math.min(...layouts.filter(k => needs[k]).map(k => Math.floor(counts[k] / needs[k])));
      const short = layouts.map(k => Math.max(0, 7 * needs[k] - counts[k]));
      const reserveShort = layouts.reduce((n, k) => n + Math.max(0, reserves[k] - counts[k]), 0);
      return {status: warning || days < 1 || reserveShort ? 'at_risk' : 'covered',
        message: `日常合格库存 ${stock} 条（竖屏 ${counts.portrait}、横屏 ${counts.landscape}）。按每日竖屏 ${needs.portrait}、横屏 ${needs.landscape} 折算 ${days} 天；未来七天还缺竖屏 ${short[0]}、横屏 ${short[1]} 条。${reserveShort ? `当前储备目标还缺 ${reserveShort} 条。` : ''}发布前仍需复验，不代表已经发布。${experimentWarning}`};
    }
    if (stock < target) return {status: 'at_risk',
      message: `日常合格成片仅 ${stock} 条，低于每日 ${target} 条目标，需要补库。`};
    return {status: warning ? 'at_risk' : 'covered', message: `日常合格库存 ${stock} 条，可覆盖至少一天的 ${target} 条目标；不代表已经发布。${experimentWarning}`};
  }
  const api = {latestDispatches, remaining, reconcile, continuity};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ProductionStatus = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
