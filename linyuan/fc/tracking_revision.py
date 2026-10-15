"""Mainland transport for the exact independently accepted tracking repair."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile
from tracking_revision_contract import BVID,NEW_SHA,ARTIFACT_ID
from replace_reported_tracking import apply as edit,client_from_cookies


def apply(fc):
    with tempfile.TemporaryDirectory(prefix='tracking-revision-') as folder:
        root=Path(folder);archive=root/'delivery.zip'
        fc.download_reviewed_zip(ARTIFACT_ID,archive,attempts=2)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                name=entry.filename
                if entry.is_dir() or Path(name).name!=name or '\\' in name:continue
                if name=='meta.json' or name.endswith(('.mp4','.ass','.jpg','.json')):
                    (root/name).write_bytes(z.read(entry))
        def validate(meta,directory):
            final=Path(directory)/'final.mp4'
            if (meta.get('final')!='final.mp4' or not final.is_file()
                    or hashlib.sha256(final.read_bytes()).hexdigest()!=NEW_SHA
                    or (meta.get('fingerprints') or {}).get('sha256')!=NEW_SHA):
                return 'Actual accepted repair MP4 hash changed'
            proof=meta.get('tracking_revision') or {}
            if proof.get('bvid')!=BVID or proof.get('independent_media_validation_passes')!=2:
                return 'Independent repair evidence missing'
            error=(fc.artifact_quality_error(meta) or fc.artifact_subtitle_error(meta,root)
                   or fc.artifact_cover_error(meta,root)
                   or fc.editorial.metadata_error(meta,fc.mp4_duration(final)))
            if error:return error
            subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(final),
                '-map','0:v:0','-map','0:a:0','-f','null','-'],check=True,capture_output=True,timeout=180)
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(final),
                '-map','0:a:0','-c:a','copy','-f','adts','pipe:1'],timeout=60)
            if hashlib.sha256(raw).hexdigest()!='d196fa6037e73228cf215a80dce986d186e72b68828fc2c24eb6f64fdb30471c':
                return 'Original encoded speech audio changed'
            return None
        client,csrf=client_from_cookies(fc.OWNER_MID)
        return edit(fc,root,client,csrf,validate)
