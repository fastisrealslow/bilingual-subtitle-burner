const test = require('node:test');
const assert = require('node:assert/strict');
const {reconcile, remaining, continuity} = require('../site/production-status.js');
const now = 1789531200;
const keys = ['ts','failed','published_parts','processed_part_indices','source_check_retry_after','weekly_full_week'];
function snapshot(entry, status) {
  return {updated_at:now,tasks:[{slug:entry.slug,status,detail:'checked',
    signature:Object.fromEntries(keys.map(k=>[k,entry[k]??null]))}]};
}

test('latest terminal row removes eight ghost pending rows',()=>{
  const latest={slug:'ly-0906-12a951',ts:9,failed:true};
  const dispatched=[latest,...Array.from({length:8},(_,ts)=>({slug:latest.slug,ts}))];
  const result=reconcile({dispatched,published:{}},snapshot(latest,'failed'),now);
  assert.equal(result.tasks.length,1);
  assert.equal(result.failures.length,1);
  assert.equal(result.pending.length,0);
});
test('paused history and reserved whole interview are distinct',()=>{
  const e={slug:'mother',ts:1};
  assert.equal(reconcile({dispatched:[e]},snapshot(e,'paused'),now).history.length,1);
  assert.equal(reconcile({dispatched:[e]},snapshot(e,'reserved'),now).pending.length,1);
});
test('stale or unavailable snapshot never invents a running job',()=>{
  const e={slug:'mother',ts:1};
  const old=snapshot(e,'running');old.updated_at=now-4*3600;
  for (const data of [null,old]) {
    const result=reconcile({dispatched:[e]},data,now);
    assert.equal(result.pending.length,0);
    assert.equal(result.tasks[0].status,'unknown');
  }
});
test('state write after snapshot supersedes stale running status',()=>{
  const e={slug:'mother',ts:1};
  const result=reconcile({dispatched:[{...e,failed:true}]},snapshot(e,'running'),now);
  assert.equal(result.tasks[0].status,'failed');
});
test('new publication receipt beats a previously ready snapshot',()=>{
  const e={slug:'mother',ts:1};
  const result=reconcile({dispatched:[e],published:{mother:{bvid:'BVdone',parts_total:1}}},snapshot(e,'ready'),now);
  assert.equal(result.tasks[0].status,'complete');
  assert.equal(result.pending.length,0);
});
test('noncontiguous processed indices and single published receipts close batches',()=>{
  assert.equal(remaining({slug:'m'}, {published:{m:{parts_total:1,bvid:'BVdone'}}}),false);
  assert.equal(remaining({slug:'m',published_parts:1,processed_part_indices:[1,2]},
    {published:{m:{parts_total:3}}}),false);
  assert.equal(remaining({slug:'m',published_parts:1,processed_part_indices:[2]},
    {published:{m:{parts_total:3}}}),true);
});

function supply(stock, target = 4) {
  return {updated_at:now, inventory_updated_at:now,
    continuity:{verified_daily_stock:stock,daily_target:target,quality_gates_relaxed:false}};
}
test('zero daily stock warns even when a weekly interview and active jobs exist',()=>{
  const data=supply(0);
  data.inventory={verified_weekly_full:1};
  data.continuity.active_or_recovering_sources=6;
  assert.equal(continuity(data,now).status,'stockout');
  assert.match(continuity(data,now).message,/每日 4 条/);
});
test('daily supply warns below target and does not claim publication when covered',()=>{
  assert.equal(continuity(supply(3),now).status,'at_risk');
  assert.equal(continuity(supply(4),now).status,'covered');
  assert.match(continuity(supply(4),now).message,/不代表已经发布/);
});
test('seven-day floor shortfall cannot be hidden by active production',()=>{
  const data=supply(0);
  data.continuity.plan={minimum_week_shortfall:14};
  data.continuity.active_or_recovering_sources=6;
  assert.match(continuity(data,now).message,/每天至少 2 条、争取 3 条/);
  assert.match(continuity(data,now).message,/未来七天至少缺 14 条/);
});
test('missing, stale, malformed or relaxed inventory is never treated as covered',()=>{
  const stale=supply(12);stale.inventory_updated_at=now-4*3600;
  const relaxed=supply(12);relaxed.continuity.quality_gates_relaxed=true;
  const future=supply(12);future.updated_at=now+120;
  for (const data of [null,{},stale,relaxed,future,supply(null),supply('12'),supply(-1),supply(12,0)])
    assert.equal(continuity(data,now).status,'unknown');
});
