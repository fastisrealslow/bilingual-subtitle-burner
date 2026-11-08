"""Rollout is explicit; a debt-blocked/unverified deployment cannot activate it."""
VERSION=2026110801
TARGET_RATIO=0.10
# Enable only after packaged runtime, worker protocol and CI preview pass.
RUNNER_PUBLISH_ENABLED=False
# Analytics already runs independently on CI. The half-hour backup replaces the
# paid hourly FC wake-up without changing 24/72/168h observation windows.
CI_OBSERVATION_CLOCK=True


def estimate_compute(seconds,calls=1,*,cpu=0.5,memory_gb=1):
    """CU estimate, not a bill: excludes hour minimum, CDT and discounts."""
    if seconds<0 or calls<0:raise ValueError('Negative FC usage')
    return seconds*(cpu+0.15*memory_gb)+calls*0.0075
