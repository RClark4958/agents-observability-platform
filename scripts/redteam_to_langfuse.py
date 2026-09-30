"""Publish the red-team seed pack and run results to the local Langfuse as a dataset.

Each seed becomes a dataset item (input: caller, turns, plants, poisoned tool; expected output:
which detectors define success, or the benign expectation). Each run record is linked to its
item as a dataset run named after the defense config, pointing at the run's OpenTelemetry trace,
so Langfuse shows every conversation and trace per seed, per config.

    uv run python scripts/redteam_to_langfuse.py [data/redteam/runs.jsonl]

Uses the public REST API with LANGFUSE_LOCAL_PUBLIC_KEY / LANGFUSE_LOCAL_SECRET_KEY from .env.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from payerbench.redteam.cases import load
from payerbench.redteam.run import load as load_runs

HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
DATASET = "payerbench-redteam-seeds"


def _post(path: str, body: dict) -> dict:
    auth = f"{os.environ['LANGFUSE_LOCAL_PUBLIC_KEY']}:{os.environ['LANGFUSE_LOCAL_SECRET_KEY']}"
    req = urllib.request.Request(
        f"{HOST}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{path}: HTTP {e.code} {e.read()[:300]!r}") from e


def main(runs_path: str = "data/redteam/runs.jsonl") -> None:
    _post(
        "/api/public/v2/datasets",
        {"name": DATASET, "description": "PayerBench adversarial seed pack (P3)"},
    )
    for c in load():
        _post(
            "/api/public/dataset-items",
            {
                "datasetName": DATASET,
                "id": f"seed-{c.id}",
                "input": {
                    "caller": c.caller,
                    "turns": c.turns,
                    "plants": c.plants,
                    "poison_tool": c.poison_tool,
                },
                "expectedOutput": {"expect": c.expect}
                if c.benign
                else {"attack_succeeds_if": c.succeeds_if},
                "metadata": {"category": c.category},
            },
        )
    n = 0
    for r in load_runs(Path(runs_path)):
        _post(
            "/api/public/dataset-run-items",
            {
                "runName": f"{r['config']}-rep{r['rep']}",
                "runDescription": f"defenses={r['config']}, model={r['model']}",
                "datasetItemId": f"seed-{r['case_id']}",
                "traceId": r["trace_id"],
                "metadata": {
                    "success": r["success"],
                    "utility": r.get("utility"),
                    "detectors": list(r["detectors"]),
                },
            },
        )
        n += 1
    print(f"dataset {DATASET}: {len(load())} items, {n} run items linked")


if __name__ == "__main__":
    main(*sys.argv[1:])
