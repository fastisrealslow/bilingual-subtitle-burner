(() => {
  const target = document.getElementById('creator-metrics');
  if (!target) return;
  const valid = (value) => Number.isInteger(value) && value >= 0;
  const count = (value) => valid(value) ? value.toLocaleString('zh-CN') : '—';
  const rate = (value) => valid(value) && value <= 10000 ? (value / 100).toFixed(2) + '%' : '—';
  const el = (tag, value) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = value;
    return node;
  };
  async function render() {
    try {
      const response = await fetch('./site/creator_metrics.json', {cache: 'no-store'});
      if (!response.ok) throw new Error('unavailable');
      const data = await response.json();
      if (data.schema !== 1 || !Array.isArray(data.videos) || !data.videos.length) {
        target.textContent = '暂无可展示的创作指标。';
        return;
      }
      const when = Date.parse(data.observed_at);
      if (!Number.isFinite(when)) throw new Error('missing timestamp');
      const label = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai',
        year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'}).format(when);
      const stale = Date.now() - when > 14 * 3600000;
      const note = el('p', (stale ? '数据暂未刷新；' : '') +
        '采集于 ' + label + '（北京时间）。仅含本号近期能确认公开且有后台数据的稿件，不是全量；' +
        '统计窗口由 B 站接口决定，未核实为发布后 7 天。— 表示未取得，非 0。平台转粉率使用 B 站自身口径，不等于本表两列直接相除。平均观看时长的单位仍待核实。');
      const wrap = el('div');
      wrap.style.overflowX = 'auto';
      const table = el('table');
      table.className = 'ps-table';
      const head = el('thead'), headings = el('tr');
      for (const name of ['稿件','播放','新增关注','平台转粉率','封面点击率','3秒退出率','完播率','平均观看时长','互动率']) {
        headings.appendChild(el('th', name));
      }
      head.appendChild(headings);
      table.appendChild(head);
      const body = el('tbody');
      for (const row of data.videos) {
        if (!/^BV[A-Za-z0-9]{10}$/.test(row.bvid)) continue;
        const tr = el('tr'), td = el('td'), link = el('a', row.title || row.bvid);
        link.href = 'https://www.bilibili.com/video/' + row.bvid;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        td.appendChild(link);
        tr.appendChild(td);
        for (const value of [count(row.plays), count(row.new_follows), rate(row.follow_rate_bps),
          rate(row.thumbnail_ctr_bps), rate(row.early_exit_bps), rate(row.completion_bps),
          valid(row.average_watch_time_raw) ? '待核口径' : '—', rate(row.engagement_bps)]) {
          tr.appendChild(el('td', value));
        }
        body.appendChild(tr);
      }
      table.appendChild(body);
      wrap.appendChild(table);
      target.replaceChildren(note, wrap);
    } catch {
      target.textContent = '单稿数据暂不可读，不影响原有统计。';
    }
  }
  render();
})();
