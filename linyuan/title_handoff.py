"""Reuse an exact title-stage result; never turn a replay into editorial approval."""
import hashlib
import json
from pathlib import Path
import re


def load_result(path, identity, source_sha256, model_digest=None):
    raw=Path(path).read_bytes()
    row=json.loads(raw)
    if not isinstance(row,dict) or row.get('status')!='generated' or row.get('experiment_valid') is not True:
        raise ValueError('独立标题任务没有完整生成结果')
    if (not re.fullmatch(r'[a-f0-9]{64}',source_sha256 or '')
            or row.get('source_sha256')!=source_sha256):
        raise ValueError('独立标题任务与当前母片字节不一致')
    if row.get('transcript_sha256')!=identity['transcript_sha256']:
        raise ValueError('独立标题任务与本次完整选段字幕不一致')
    result=row.get('result')
    if not isinstance(result,dict) or result.get('copy_identity')!=identity:
        raise ValueError('独立标题任务的代码、模型、人物或配置已经变化')
    if identity.get('text_backend')=='local' and (
            not model_digest or row.get('model_digest')!=model_digest):
        raise ValueError('独立标题任务与当前本地模型权重摘要不一致')
    if row.get('proof_error') is not None or result.get('title_quality_verified') is not True:
        raise ValueError('独立标题任务没有通过标题证据核验')
    proof=dict(version=1,record_sha256=hashlib.sha256(raw).hexdigest(),
        generation_commit=row.get('commit'),generation_run_id=row.get('run_id'),
        model_digest=row.get('model_digest'),
        source_sha256=source_sha256,transcript_sha256=row['transcript_sha256'],
        title=result.get('title'),cover_title=result.get('cover_title'),
        editorial_approved=False,
        scope='Exact model result reuse; current title gate and full video gates still required')
    return result,proof
