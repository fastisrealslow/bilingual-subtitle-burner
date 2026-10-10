"""The new default is neutral, not original; reviewed historical uploads stay fixed."""
import ast
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def constant(file,name):
    for node in ast.parse((ROOT/file).read_text()).body:
        if not isinstance(node,ast.Assign):continue
        target=node.targets[0]
        if isinstance(target,ast.Name) and target.id==name:return ast.literal_eval(node.value)
        if isinstance(target,ast.Tuple):
            for index,field in enumerate(target.elts):
                if isinstance(field,ast.Name) and field.id==name:return ast.literal_eval(node.value.elts[index])
    raise AssertionError(name)


def test_new_publishers_default_to_unselected_not_original():
    assert constant('linyuan/fc/index.py','COPYRIGHT')==3
    assert constant('linyuan/publish_worker.py','COPYRIGHT')==3
    assert constant('linyuan/publish_bilibili_cn.py','DEFAULT_COPYRIGHT')==3


def test_regular_publisher_keeps_source_and_records_requested_not_unverified_actual_value():
    code=(ROOT/'linyuan/fc/index.py').read_text()
    assert '"--copyright", str(COPYRIGHT)' in code
    assert '"--source", publication_source_label(' in code
    assert '"copyright_requested": COPYRIGHT' in code
    assert '"--copyright", "2"' in code  # Explicitly reviewed historical mode is separate.
