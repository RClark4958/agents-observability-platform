"""Judge calibration study (roadmap project 2).

Pipeline: generate scenarios with ground truth from the synthetic world -> collect agent answers
-> grade deterministically to produce gold labels (with a human override column) -> run a panel of
judges repeatedly -> score agreement, repeatability and calibration -> report.
"""
