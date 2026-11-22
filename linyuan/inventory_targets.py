"""User's Nov 20 backlog goals, separate from daily publication safety floors.

Only inspected, distinct, unconsumed, unexpired daily live deliveries count.
An ordinary fallback never fills the treatment goal; running jobs never count.
"""
VERSION = 2026112001
EFFECTIVE_FROM = '2026-11-20'
ORDINARY_TARGET = 900
TREATMENT_TARGET = 300
SEED_TARGET = 10000
TREATMENT_MIN_LANDSCAPE = 150


def active(now=None):
    import time
    return time.strftime('%Y-%m-%d',time.gmtime((time.time() if now is None else now)+8*3600))>=EFFECTIVE_FROM


def layout_counts(value):
    if (not isinstance(value,dict) or any(type(value.get(k)) is not int or value[k]<0
            for k in ('portrait','landscape'))):
        return None
    return {k:value[k] for k in ('portrait','landscape')}


def ordinary_deficits(inventory):
    """None is unknown, not permission to schedule 900 speculative deliveries."""
    counts=layout_counts(inventory.get('ordinary_ready'))
    if not inventory.get('inventory_fresh') or counts is None:return None
    # Ordinary slots accept verified native landscape as well as portrait.
    # Do not force a 750/150 split or manufacture 900 portrait crops. Auto
    # rendering chooses the source's real geometry; this is only an intent.
    return dict(portrait=max(0,ORDINARY_TARGET-sum(counts.values())),landscape=0)


def progress(inventory,seed_count=None):
    ordinary=layout_counts(inventory.get('ordinary_ready'))
    treatment=layout_counts(inventory.get('experiment_ready'))
    fresh=inventory.get('inventory_fresh') is True
    known=(fresh and ordinary is not None and treatment is not None
           and type(inventory.get('daily_mix_usable')) is int
           and sum(ordinary.values())+sum(treatment.values())==inventory['daily_mix_usable'])
    def row(target,counts):
        verified=sum(counts.values()) if known else None
        return dict(target=target,verified=verified,shortfall=max(0,target-verified) if known else None,
                    by_layout=counts if known else None)
    normal=row(ORDINARY_TARGET,ordinary)
    experimental=row(TREATMENT_TARGET,treatment)
    normal['layout_policy']='native_geometry_flexible_control_slots'
    experimental['minimum_landscape']=TREATMENT_MIN_LANDSCAPE
    achieved=(known and not any(ordinary_deficits(inventory).values())
              and experimental['verified']>=TREATMENT_TARGET
              and treatment['landscape']>=TREATMENT_MIN_LANDSCAPE
              and type(seed_count) is int and seed_count>=SEED_TARGET)
    return dict(version=VERSION,status='unknown' if not known else 'achieved' if achieved else 'replenishing',
        ordinary=normal,treatment=experimental,
        seeds=dict(target=SEED_TARGET,verified=seed_count if type(seed_count) is int and seed_count>=0 else None,
                   shortfall=max(0,SEED_TARGET-seed_count) if type(seed_count) is int and seed_count>=0 else None),
        seed_target=SEED_TARGET,
        in_flight_counted=False,quality_gates_relaxed=False,
        counting_policy='distinct_verified_unconsumed_unexpired_daily_live_files_disjoint_profiles')
