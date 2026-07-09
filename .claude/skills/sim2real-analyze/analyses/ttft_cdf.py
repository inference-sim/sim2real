#!/usr/bin/env python3
"""TTFT empirical CDF, baseline vs treatment, one panel per workload.

Invocation:
    python .claude/skills/sim2real-analyze/analyses/ttft_cdf.py --run <name>
    # --run defaults to current_run from workspace/setup_config.json
"""
from _cdf import cdf_main


if __name__ == "__main__":
    cdf_main("TTFT")
