"""Independent artifact-byte acceptance; never promotes or publishes previews."""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import publishing_experiment as E


def verify(directory):
    directory=Path(directory)
    data=json.loads((directory/'meta.json').read_text())
    rows=data if isinstance(data,list) else [data]
    accepted=[]
    for row in rows:
        if E.actual_profile(row)!=E.TREATMENT:continue
        for field,digest in [('final',(row.get('fingerprints') or {}).get('sha256')),
                             ('cover',(row.get('cover_proof') or {}).get('sha256'))]:
            name=row.get(field)
            if not isinstance(name,str) or Path(name).name!=name:raise ValueError('Invalid artifact path')
            path=directory/name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('Actual experiment bytes do not match proof: '+field)
        accepted.append(dict(title=row['title'],final=row['final'],cover=row['cover'],
            sha256=row['fingerprints']['sha256'],selection=row['experiment_proof']['selection'],
            source_sha256=row['source_sha256']))
    if not accepted:raise ValueError('No verified experimental delivery; baseline fallback is NOT experiment success')
    return dict(experiment_complete=True,selection_revision=E.SELECTION_REVISION,accepted=accepted)


if __name__=='__main__':
    result=verify(sys.argv[1])
    (Path(sys.argv[1])/'experiment_acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
