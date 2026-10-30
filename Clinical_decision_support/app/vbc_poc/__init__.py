"""Value-Based Care POC: a longitudinal measurement-and-action loop.

Collect history -> normalize evidence -> evaluate measures -> create care gaps -> prioritize
risk -> assign actions -> capture closure -> measure impact.

HEDIS-inspired prototype logic on synthetic data. Not an NCQA-certified HEDIS product.
"""

import os

FEATURE_FLAG = "VBC_POC_ENABLED"


def enabled() -> bool:
    """One switch disables the POC pages and API without touching the encounter flow."""
    return os.environ.get(FEATURE_FLAG, "1").strip().lower() not in ("0", "false", "no", "off")
