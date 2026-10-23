"""Resume scheduling only: no prior semantic verdict is an approval."""


def continuation_order(candidates, prior, *, transcript_sha256, revision):
    original=list(range(len(candidates)))
    evidence=dict(applied=False,prior_approvals_reused=False,pending_first=[])
    try:
        if (not isinstance(prior,dict) or prior.get('revision')!=revision
                or prior.get('transcript_sha256')!=transcript_sha256
                or prior.get('retryable') is not True
                or prior.get('selection_completed') is not False
                or type(prior.get('candidate_count')) is not int
                or prior['candidate_count']!=len(candidates)):
            return original,evidence
        def spans(rows):
            result=[]
            for p in rows:
                a,b=p['start'],p['end']
                if type(a) is not int or type(b) is not int or not 0<=a<=b:
                    raise ValueError('Invalid source cue interval')
                result.append((a,b))
            if len(set(result))!=len(result):raise ValueError('Ambiguous duplicate intervals')
            return result
        before=spans(prior['candidates']);after=spans(candidates)
        if len(before)!=len(after) or set(before)!=set(after):return original,evidence
        unresolved=prior['unresolved']
        if not isinstance(unresolved,list):return original,evidence
        pending=set()
        for row in unresolved:
            i=row['index']
            if type(i) is not int or not 0<=i<len(before):return original,evidence
            if row.get('error')=='selection_budget_exhausted':pending.add(before[i])
        first=[i for i,p in enumerate(after) if p in pending]
        if not first:return original,evidence
        return first+[i for i,p in enumerate(after) if p not in pending],{
            **evidence,'applied':True,'pending_first':first}
    except (TypeError,KeyError,ValueError,AttributeError):
        # Missing/mismatched scheduling hints are only a cold start. Never
        # discard candidates, import verdicts, or fail a verified new source.
        return original,evidence
