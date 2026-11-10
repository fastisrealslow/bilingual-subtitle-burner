"""Rollout is explicit; a debt-blocked/unverified deployment cannot activate it."""
VERSION=2026111001
TARGET_RATIO=0.10
# Enable only after packaged runtime, worker protocol and CI preview pass.
RUNNER_PUBLISH_ENABLED=False
UPLOAD_BACKEND="ci-preferred"
# Analytics already runs independently on CI. The half-hour backup replaces the
# paid hourly FC wake-up without changing 24/72/168h observation windows.
CI_OBSERVATION_CLOCK=True


def estimate_compute(seconds,calls=1,*,cpu=0.5,memory_gb=1):
    """CU estimate, not a bill: excludes hour minimum, CDT and discounts."""
    if seconds<0 or calls<0:raise ValueError('Negative FC usage')
    return seconds*(cpu+0.15*memory_gb)+calls*0.0075

# The visible schedule remains 10/14/16/21. After live acceptance, standard CI
# owns it; one late domestic recovery can fill any missed original slots.
CI_CLOCK_ENABLED=False
RECOVERY_TRIGGER='publish-recovery'
LEGACY_PUBLISH_CRON='0 0 2,6,8,13 * * *'
RECOVERY_CRON='0 55 13 * * *'


def publish_timer():
    recovery=RUNNER_PUBLISH_ENABLED and CI_CLOCK_ENABLED
    return dict(cron=RECOVERY_CRON if recovery else LEGACY_PUBLISH_CRON,
        payload=dict(triggerName=RECOVERY_TRIGGER,purpose='last_chance_daily_four')
                if recovery else dict(triggerName='publish'),
        recovery=recovery)
