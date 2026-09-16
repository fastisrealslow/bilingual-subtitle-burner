const test = require('node:test');
const assert = require('node:assert/strict');
const {reconcile, remaining} = require('../site/production-status.js');
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
