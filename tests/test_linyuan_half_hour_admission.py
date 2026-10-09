"""Half-hour candidate admission is separate from publication and its quota."""
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import index as FC


def test_half_hour_runner_keeps_single_queue_and_existing_capacity_limits():
    workflow=(ROOT/'.github/workflows/linyuan-dispatch-runner.yml').read_text()
    assert "cron: '7,37 * * * *'" in workflow
    assert 'group: linyuan-dispatch-runner' in workflow
    assert 'cancel-in-progress: false' in workflow
    assert 'dispatch_on_runner.py' in workflow
    assert FC.MAX_ACTIVE_SOURCES==6
    assert FC.MAX_PUBLISH_PER_DAY==4


def test_publish_timer_remains_four_slots_not_half_hour_forced_posting():
    script=(ROOT/'linyuan/fc/verify_production.py').read_text()
    assert "'publish':'0 0 2,6,8,13 * * *'" in script
    assert "name == 'dispatch' else True" in script
