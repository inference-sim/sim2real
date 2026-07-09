#!/usr/bin/env python3
"""TPOT empirical CDF, baseline vs treatment, one panel per workload.

Invocation:
    python .claude/skills/sim2real-analyze/analyses/tpot_cdf.py --run <name>
    # --run defaults to current_run from workspace/setup_config.json

TPOT is only defined for requests with output_tokens > 1; rows below that
threshold are excluded per-phase before the CDF is computed.
"""
from _cdf import cdf_main


if __name__ == "__main__":
    cdf_main("TPOT")
