"""Try the measured CI route; use FC only with proof of no prior submission."""
from pathlib import Path
import time

from ci_upload import UploadNotSubmitted,execute as upload_on_ci
from upload_transport import RemoteUploadPending
import reviewed_upload_client as domestic


class UploadDefinitelyNotStarted(RuntimeError):
    """Neither primary submission nor any domestic task was started."""


def execute(fc,state,entry,part,cmd,directory):
    sha=(part.get('fingerprints') or {}).get('sha256')
    try:
        return upload_on_ci(cmd,sha,fc.OWNER_MID)
    except RemoteUploadPending as exc:
        evidence=getattr(exc,'safe_evidence',None)
        if evidence:
            entry['upload_ci_outcome']=evidence
            fc.save_state(state)
        raise
    except UploadNotSubmitted as exc:
        entry['upload_ci_pre_submit_failure']=dict(error_type=str(exc)[:100],checked_at=int(time.time()))
    # A health probe uses the legacy read-only route, never a publisher event.
    try:
        if not domestic.capability():raise RuntimeError('Domestic runtime is not current')
        binding=domestic.prepare(fc,Path(directory),part,entry,cmd)
        binding['video_sha256']=sha
        if fc.save_state(state) is False:raise RuntimeError('Fallback intent was not saved')
    except Exception as exc:
        # The domestic invocation has not been reached. Releasing this one
        # attempt is safe, unlike releasing an unknown remote/submit response.
        raise UploadDefinitelyNotStarted(type(exc).__name__) from exc
    return domestic.execute(binding)
