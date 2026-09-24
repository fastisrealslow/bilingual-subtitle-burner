"""Build the same complete runtime archive for production and staging."""
import argparse
import ast
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    'fc/index.py', 'stage_context.py', 'title_rewrite.py', 'artifact_range.py',
    'title_quantity_context.py', 'title_market_impression.py',
    'speaker_attribution.py', 'headline_policy.py', 'editorial_policy.py',
    'caption_readability.py', 'presentation.py', 'live_motion.py',
    'source_geometry.py', 'source_priority.py', 'production_diagnostics.py',
    'fc/media_repair.py', 'fc/stage_revision.py', 'fc/title_revision.py',
    'fc/reviewed_updates.py', 'fc/reviewed_third_video.py',
)


def build(destination):
    sources = [ROOT / name for name in SOURCES]
    names = {p.stem for p in sources}
    # Check lazy imports too: these used to fail only when FC validated a title.
    for path in sources:
        for node in ast.walk(ast.parse(path.read_text())):
            imports = ([node.module] if isinstance(node, ast.ImportFrom) and node.module
                       else [a.name for a in node.names] if isinstance(node, ast.Import) else [])
            for module in imports:
                name = module.split('.')[0]
                if name not in names and any((base / (name+'.py')).is_file()
                                            for base in (ROOT, ROOT/'fc')):
                    raise ValueError(f'{path.name} requires missing runtime module {name}')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sources:
            archive.write(path, path.name)
        for path in sorted((ROOT/'fc/reviewed_0910').rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(ROOT/'fc'))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip():
            raise ValueError('FC archive CRC check failed')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination')
    print(build(parser.parse_args().destination))
