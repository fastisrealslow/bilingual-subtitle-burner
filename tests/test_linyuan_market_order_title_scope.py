"""An actual accepted title reversed the speaker's comparison and denial."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T


def test_actual_title_and_cover_cannot_reverse_comparison_or_move_its_denial():
    f=json.loads((Path(__file__).parent/'fixtures/linyuan_actual_primary_secondary_market_title.json').read_text())
    source=f['transcript'];good='林园：别把一级市场更贵当常识，定价权在二级市场'
    assert T.forecast_copy_error(f['rejected_title'],'二级市场有定价权',source)
    assert T.forecast_copy_error(good,f['rejected_cover'],source)
    assert T.market_order_scope_error(good,'二级市场有定价权',source) is None
    assert T.market_order_scope_error('林园：一级市场就该比二级贵？这是瞎扯','别把一级更贵当常识',source) is None
    assert T.market_order_scope_error(f['rejected_title'],f['rejected_cover'],
        '二级市场应该比一级市场贵，我说那是瞎扯。') is None
