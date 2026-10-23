"""Shared production/diagnostic audit settings; thinking is opt-in only."""
import os


def settings(env=None):
    value=(os.environ if env is None else env).get('LINYUAN_TRANSCRIPT_AUDIT_THINKING','false')
    if value not in ('true','false'):
        raise ValueError('Transcript audit thinking must be explicitly true or false')
    thinking=value=='true'
    return dict(thinking=thinking,max_tokens=4096 if thinking else 1200,
                cloud_seconds=300 if thinking else 180)


def model_call(llm,text_budget,prompt,schema,api_key='',*,read_cache=True):
    config=settings()
    kwargs=dict(temperature=0,max_tokens=config['max_tokens'],
        budget_sec=text_budget(config['cloud_seconds']),response_schema=schema,
        read_cache=read_cache)
    if config['thinking']:kwargs['thinking']=True
    return llm([dict(role='user',content=prompt)],api_key,**kwargs)
