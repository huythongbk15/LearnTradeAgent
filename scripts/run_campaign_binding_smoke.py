"""Run a one-fold, one-candidate local campaign without publication.

Pass data-file/out-root and train/val/test months as normal campaign options.
This checks execution plumbing, not strategy qualification.
"""

import json
import runpy
import sys
from pathlib import Path


if __name__ == "__main__":
    campaign = Path(__file__).with_name("run_real_wfo_campaign.py")
    sys.argv = [
        str(campaign),
        *sys.argv[1:],
        "--n-outer",
        "1",
        "--no-publish",
        "--param-grid",
        json.dumps({"fast_period": [10], "slow_period": [60]}),
    ]
    runpy.run_path(str(campaign), run_name="__main__")
