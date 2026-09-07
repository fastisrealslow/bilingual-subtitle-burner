from linyuan.local_daily_controller import build_plan


def video(index, mode='live_video_card', qualified=True):
    return {'file': f'/tmp/{index}.mp4', 'render_mode': mode, 'qualified': qualified}


def test_plan_refuses_preliminary_windows_and_incomplete_batch():
    audit = {'videos': [video(1)]}
    plan = build_plan(audit, {'candidate_count': 54}, published_today=1)
    assert plan['cloud_calls'] == 0
    assert plan['today']['remaining'] == 5
    assert plan['local_inventory']['preliminary_source_windows'] == 54
    assert not plan['publish_enabled']
    assert plan['selected_files'] == []


def test_plan_requires_five_live_and_allows_one_audio():
    rows = [video(i) for i in range(5)] + [video(5, 'audio_card')]
    plan = build_plan({'videos': rows})
    assert plan['next_batch_ready']
    assert len(plan['selected_files']) == 6


def test_plan_accepts_six_live_videos():
    plan = build_plan({'videos': [video(i) for i in range(6)]})
    assert plan['next_batch_ready']
    assert len(plan['selected_files']) == 6


def test_plan_rejects_two_audio_cards_even_with_six_files():
    rows = [video(i) for i in range(4)] + [video(4, 'audio_card'), video(5, 'audio_card')]
    plan = build_plan({'videos': rows})
    assert not plan['next_batch_ready']
