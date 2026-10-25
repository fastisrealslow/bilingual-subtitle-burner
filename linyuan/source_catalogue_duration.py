"""Discovery follows the existing content floor; chapters keep bumper filtering."""
import math

from editorial_policy import MIN_SECONDS


def eligible_duration(seconds, page_count=1):
    """A standalone source may be short; archive parts retain the 120s floor.

    This admits catalogue metadata only. It grants no speaker, picture,
    subtitle, originality, experiment or publication approval.
    """
    if (type(seconds) not in (int, float) or not math.isfinite(seconds)
            or type(page_count) is not int or page_count < 1):
        return False
    minimum = MIN_SECONDS if page_count == 1 else max(120.0, MIN_SECONDS)
    return minimum <= seconds <= 5400
