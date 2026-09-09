"""Replay preserved #612-615 responses; this is not content approval."""
import argparse
import json
from pathlib import Path
import produce_cn as production


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    incidents={34324232881:10,34324248124:6,34324263270:9,34324276167:3}
    malformed={(34324248124,'context_response_block_1.txt'),
               (34324263270,'context_response_block_3.txt')}
    results=[]
    for run,expected_count in incidents.items():
        root=args.root/str(run)
        cues=json.loads(next(root.rglob('cues_raw.json')).read_text())
        paths=sorted(root.rglob('highlight_response*.txt'))+sorted(root.rglob('context_response*.txt'))
        assert len(paths)==expected_count,(run,len(paths))
        for path in paths:
            try:
                rows=production.parse_llm_json_array(path.read_text())
            except RuntimeError:
                assert (run,path.name) in malformed,(run,path.name)
                results.append(dict(run=run,file=path.name,status='malformed_requires_retry'))
                continue
            assert (run,path.name) not in malformed
            assert isinstance(rows,list)
            results.append(dict(run=run,file=path.name,status='parsed_only_not_approved'))
        # Candidate lengths are derived from the preserved ASR timestamps.
        choices=production.argument_context_candidates(cues,[dict(start=0,end=0)])
        assert choices
        for choice in choices:
            assert production.editorial.range_seconds(cues,choice)>=120
    print(json.dumps(dict(responses=len(results),parsed=sum(r['status'].startswith('parsed') for r in results),
                         malformed=len(malformed),content_approved=False,cases=results),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
