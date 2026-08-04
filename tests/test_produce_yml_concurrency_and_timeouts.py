"""解析 .github/workflows/produce.yml 的断言测试（用 PyYAML 读文件后断言字段值）。

覆盖四处修复里的三处 YAML 配置问题：

1. produce job 缺并发保护：两次同 slug 的出片任务重叠时，双方都会
   `gh release upload --clobber` 覆盖同名 asset，同时改
   site/data/index.json，后完成的盖掉先完成的。要求：produce job 有
   按 slug 区分的 concurrency 组，且 ``cancel-in-progress`` 是 false
   （排队等待，不是取消——取消会白扔掉已付过的下载/转写/LLM 开销）。

3. plan job 没有 timeout-minutes，会走 GitHub 默认的 360 分钟上限。

4. 索引推送重试次数与退避总时长。

这里只核 YAML 里写的字段值，不解释 GitHub Actions 引擎怎么执行
concurrency（那属于平台行为，不是这个仓库的代码，见任务报告里对官方文档的
引用）。
"""

from pathlib import Path

import yaml

WORKFLOW_PATH = (Path(__file__).resolve().parent.parent
                 / ".github" / "workflows" / "produce.yml")


def _load_workflow():
    with open(WORKFLOW_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_workflow_file_parses_as_valid_yaml():
    doc = _load_workflow()
    assert "jobs" in doc
    assert "plan" in doc["jobs"]
    assert "produce" in doc["jobs"]


# ── 一、produce job 的并发保护 ──────────────────────────────────────────────

def test_produce_job_has_a_concurrency_block():
    doc = _load_workflow()
    produce_job = doc["jobs"]["produce"]
    assert "concurrency" in produce_job, (
        "produce job 缺 concurrency 配置：两次同 slug 的出片任务重叠时，"
        "双方都会 --clobber 覆盖同名 Release asset 并同时改站点索引")


def test_produce_concurrency_group_is_scoped_by_slug():
    doc = _load_workflow()
    group = doc["jobs"]["produce"]["concurrency"]["group"]
    assert "matrix.job.slug" in group, (
        f"concurrency.group={group!r} 没有引用 matrix.job.slug，"
        f"不同 slug 之间会被错误地串行化，push sources/*.json 的跨 slug "
        f"并行路径会被这个改动误伤")


def test_produce_concurrency_group_is_not_a_fixed_string_shared_by_all_slugs():
    """反向断言：并发组绝不能是不含 slug 变量的固定字符串。

    固定字符串会让所有 slug 共享同一个并发组，跨 slug 并行被整体串行化，
    直接违反 push sources/*.json matrix 路径要求的跨 slug 并行。
    """
    doc = _load_workflow()
    group = doc["jobs"]["produce"]["concurrency"]["group"]
    assert group != "produce", (
        f"concurrency.group={group!r} 是不含 slug 的固定字符串，"
        f"所有 slug 会被错误地挤进同一个并发组")
    assert "${{" in group and "slug" in group


def test_produce_concurrency_cancel_in_progress_is_false():
    doc = _load_workflow()
    cancel = doc["jobs"]["produce"]["concurrency"]["cancel-in-progress"]
    assert cancel is False, (
        f"cancel-in-progress={cancel!r}，必须是 False——取消正在跑的出片任务"
        f"会白扔掉已经付过的下载/转写/LLM 开销，排队等待才对")


def test_workflow_level_has_no_conflicting_single_concurrency_group():
    """顶层不该再加一个跟 job 级冲突的单一固定并发组。"""
    doc = _load_workflow()
    assert "concurrency" not in doc, (
        "workflow 顶层出现了 concurrency——这会用单一固定组覆盖所有 slug，"
        "跟 job 级按 slug 区分的并发组语义冲突，产出规范要求放在 job 级")


# ── 三、plan job 的 timeout-minutes ────────────────────────────────────────

def test_plan_job_has_a_reasonable_timeout():
    doc = _load_workflow()
    plan_job = doc["jobs"]["plan"]
    assert "timeout-minutes" in plan_job, (
        "plan job 没有 timeout-minutes，会走 GitHub 默认的 360 分钟上限，"
        "plan_matrix.py 卡住时要白等 6 小时")
    timeout = plan_job["timeout-minutes"]
    assert 0 < timeout <= 30, (
        f"plan job timeout-minutes={timeout}，plan_matrix.py 只是汇总任务，"
        f"正常秒级完成，上限应该是个小值（如 10），不该继续沿用 360 分钟量级")


def test_produce_job_timeout_is_still_300_and_was_not_touched():
    """produce job 的 300 分钟超时是刚修过的既有值，这次改动不该动它。"""
    doc = _load_workflow()
    assert doc["jobs"]["produce"]["timeout-minutes"] == 300


# ── 四、索引推送重试次数与失败信息 ──────────────────────────────────────────

def _index_push_step(doc):
    for step in doc["jobs"]["produce"]["steps"]:
        if step.get("name") == "合并进站点索引并提交":
            return step
    raise AssertionError("没找到「合并进站点索引并提交」这个 step")


def test_index_push_retry_loop_has_more_than_five_attempts():
    doc = _load_workflow()
    script = _index_push_step(doc)["run"]
    # 旧版本是 `for i in 1 2 3 4 5; do`，累计只等 75 秒。新的循环次数必须
    # 明显更多。用具体数字断言，不用正则猜测循环体。
    assert "for i in 1 2 3 4 5; do" not in script, (
        "重试循环仍是旧的 5 次，没有被加宽")
    assert "for i in 1 2 3 4 5 6 7 8 9 10; do" in script, (
        "期望的新重试循环次数（10 次）没有出现在脚本里")


def test_index_push_step_still_exits_one_on_exhausted_retries():
    """重试耗尽后仍必须 exit 1——索引没更新等于站点看不到，必须算失败。"""
    doc = _load_workflow()
    script = _index_push_step(doc)["run"]
    lines = [ln.strip() for ln in script.strip().splitlines()]
    assert lines[-1] == "exit 1", (
        f"脚本最后一行是 {lines[-1]!r}，期望重试耗尽后仍以 exit 1 结束")


def test_index_push_failure_message_clarifies_release_already_succeeded():
    """失败信息必须明确说明成片与 Release 已发布成功，只是索引提交失败。"""
    doc = _load_workflow()
    script = _index_push_step(doc)["run"]
    assert "Release" in script and "已发布成功" in script, (
        "重试耗尽后的失败信息没有说明 Release 已经发布成功，"
        "看日志的人会误以为整批都没出来")


def test_index_push_failure_message_gives_a_remediation_path():
    """失败信息必须给出补救办法：重跑这一步或本地跑 update_site_index.py。"""
    doc = _load_workflow()
    script = _index_push_step(doc)["run"]
    assert "update_site_index.py" in script, (
        "失败信息里没有提到本地补救用的 update_site_index.py")
    assert "重跑" in script or "重新" in script, (
        "失败信息里没有提到重跑这一步/重跑 workflow 的补救办法")
