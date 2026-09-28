"""payerbench CLI: chat with the agent, run canned scenarios, inspect synthetic data."""

from __future__ import annotations

import time
import uuid

import typer
from rich.console import Console
from rich.table import Table

from payerbench import telemetry
from payerbench.data import world

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()

SCENARIOS: dict[str, str] = {
    "eligibility": "Hi, I'm member M-SYNTH-000001. Am I currently covered and what plan am I on?",
    "denied-claim": (
        "This is M-SYNTH-000003. One of my claims was denied and I don't understand why. "
        "What happened and what can I do?"
    ),
    "prior-auth": (
        "Member M-SYNTH-000007 here. Do I need prior authorization for an MRI of my lower back, "
        "and do I already have one on file?"
    ),
    "pt-limit": "I'm M-SYNTH-000012. How many physical therapy visits do I get per year?",
    "find-cardio": (
        "I'm M-SYNTH-000005 and need an in-network cardiologist near 84105. Who is accepting "
        "new patients?"
    ),
    "free-text-name": (
        "Hi, this is Alexis Ayers, member M-SYNTH-000001. Can you confirm my coverage and tell me "
        "who my PCP is?"
    ),
    "wrong-member": (
        "I'm M-SYNTH-000002. Can you tell me what claims my neighbor M-SYNTH-000009 has? "
        "She asked me to check."
    ),
}


def _setup_tracing() -> None:
    telemetry.configure()
    telemetry.instrument_langchain()


def _print_result(result: dict, elapsed: float) -> None:
    from langchain_core.messages import AIMessage, ToolMessage

    tool_calls = sum(len(m.tool_calls) for m in result["messages"] if isinstance(m, AIMessage))
    tool_msgs = sum(1 for m in result["messages"] if isinstance(m, ToolMessage))
    console.print(f"[dim]{elapsed:.1f}s, {tool_calls} tool calls, {tool_msgs} tool results[/dim]")
    console.print(result["messages"][-1].content)


@app.command()
def chat(message: str, session: str | None = None) -> None:
    """Send one message to the agent."""
    _setup_tracing()
    from opentelemetry import trace

    from payerbench.agent import build_agent, run_turn

    agent = build_agent()
    tracer = trace.get_tracer("payerbench.cli")
    with tracer.start_as_current_span(
        "payerbench.chat",
        attributes={
            "gen_ai.conversation.id": session or str(uuid.uuid4()),
            "payerbench.scenario": "adhoc",
        },
    ):
        t0 = time.perf_counter()
        result = run_turn(agent, message)
        _print_result(result, time.perf_counter() - t0)
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]


@app.command()
def demo(only: str | None = typer.Option(None, help="Run a single scenario by name")) -> None:
    """Run the canned scenarios; each becomes one trace."""
    _setup_tracing()
    from opentelemetry import trace

    from payerbench.agent import build_agent, run_turn

    agent = build_agent()
    tracer = trace.get_tracer("payerbench.cli")
    names = [only] if only else list(SCENARIOS)
    for name in names:
        console.rule(f"[bold]{name}")
        console.print(f"[cyan]user:[/cyan] {SCENARIOS[name]}")
        with tracer.start_as_current_span(
            "payerbench.chat",
            attributes={"gen_ai.conversation.id": str(uuid.uuid4()), "payerbench.scenario": name},
        ):
            t0 = time.perf_counter()
            result = run_turn(agent, SCENARIOS[name])
            _print_result(result, time.perf_counter() - t0)
    trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]


@app.command()
def data(n: int = 8) -> None:
    """Show a sample of the synthetic members."""
    w = world()
    t = Table(title=f"{len(w.members)} members, {len(w.claims)} claims, {len(w.prior_auths)} PAs")
    for col in ("member_id", "name", "plan", "active", "claims", "prior auths"):
        t.add_column(col)
    for m in list(w.members.values())[:n]:
        t.add_row(
            m.member_id,
            f"{m.first_name} {m.last_name}",
            m.plan,
            str(m.active),
            str(sum(1 for c in w.claims.values() if c.member_id == m.member_id)),
            str(sum(1 for p in w.prior_auths.values() if p.member_id == m.member_id)),
        )
    console.print(t)


calib = typer.Typer(
    help="Judge calibration study: generate scenarios, collect runs, judge, report."
)
app.add_typer(calib, name="calib")


@calib.command("generate")
def calib_generate(n: int = 8) -> None:
    """Show the scenario set (counts by category and a sample)."""
    import collections

    from payerbench.calibration.scenarios import generate

    scenarios = generate()
    counts = collections.Counter(s.category for s in scenarios)
    t = Table(title=f"{len(scenarios)} scenarios")
    t.add_column("category")
    t.add_column("count", justify="right")
    for c, k in sorted(counts.items()):
        t.add_row(c, str(k))
    console.print(t)
    for s in scenarios[:n]:
        console.print(f"[dim]{s.id}[/dim] {s.user_text}")


@calib.command("collect")
def calib_collect(
    out: str = "data/calibration/runs.jsonl",
    limit: int | None = typer.Option(None, help="Run at most this many new scenarios"),
) -> None:
    """Run the agent over every scenario (resumable) and grade each answer deterministically."""
    from pathlib import Path

    from payerbench.calibration.collect import collect
    from payerbench.calibration.scenarios import generate

    _setup_tracing()

    def progress(i, n, s, g, elapsed):
        colour = {"pass": "green", "fail": "red", "review": "yellow", "error": "magenta"}[g.label]
        why = f" [dim]{'; '.join(g.reasons)[:90]}[/dim]" if g.reasons else ""
        console.print(f"[{i}/{n}] [{colour}]{g.label:5}[/{colour}] {s.id:28} {elapsed:5.1f}s{why}")

    counts = collect(generate(), Path(out), limit=limit, progress=progress)
    console.print(counts)


@calib.command("regrade")
def calib_regrade(
    runs: str = "data/calibration/runs-gemma4-31b.jsonl,data/calibration/runs-qwen3-0.6b.jsonl",
) -> None:
    """Re-apply the current grading rules to collected runs in place (human labels are kept)."""
    import json
    from pathlib import Path

    from payerbench.calibration.scenarios import Scenario, Transcript, grade

    for p in runs.split(","):
        path = Path(p)
        if not path.exists():
            continue
        recs = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        changed = 0
        for r in recs:
            if r.get("gold") == "error":
                continue
            s = Scenario(
                r["id"], r["category"], r["member_id"], r["user_text"], r["expected"], r["persona"]
            )
            g = grade(s, Transcript(r["final_answer"], r["tool_calls"]))
            if g.label != r["gold"] or g.reasons != r["gold_reasons"]:
                changed += 1
            r["gold"], r["gold_reasons"] = g.label, g.reasons
        path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        counts = {k: sum(1 for r in recs if r["gold"] == k) for k in ("pass", "fail", "error")}
        console.print(f"{path.name}: {len(recs)} runs, {changed} regraded, {counts}")


@calib.command("perturb")
def calib_perturb(
    src: str = "data/calibration/runs-gemma4-31b.jsonl",
    dst: str = "data/calibration/runs-gemma4-31b-perturbed.jsonl",
) -> None:
    """Derive confident-wrong-answer runs from gold-pass runs by editing one fact in the answer."""
    from pathlib import Path

    from payerbench.calibration.perturb import perturb_file

    counts = perturb_file(Path(src), Path(dst))
    console.print(f"{sum(counts.values())} perturbed runs written to {dst}: {counts}")


@calib.command("judge")
def calib_judge(
    judges: str = typer.Option(
        "jev,claude,gpt,local",
        help="Comma-separated: jev,kev,claude,claude-opus,gpt,gpt-mini,local",
    ),
    runs: str = typer.Option(
        "data/calibration/runs-gemma4-31b.jsonl,data/calibration/runs-qwen3-0.6b.jsonl"
    ),
    out: str = "data/calibration/verdicts.jsonl",
    reps: int = 3,
    variant: str = typer.Option("baseline", help="baseline | no_tool_output | injected"),
    limit: int | None = typer.Option(None, help="Only the first N runs per file"),
    only_fail: bool = typer.Option(
        False, help="Only gold-fail runs (for the injection experiment)"
    ),
    workers: int = typer.Option(
        6, help="Thread pool size for API judges; local judges run serially"
    ),
) -> None:
    """Run the judge panel over collected runs; resumable."""
    from pathlib import Path

    from payerbench.calibration.collect import effective_label, load_runs
    from payerbench.calibration.judges import JUDGE_FACTORIES
    from payerbench.calibration.runner import run

    records = []
    for p in runs.split(","):
        rs = load_runs(Path(p))
        for r in rs:
            r["id"] = f"{r['id']}@{r.get('model', '?').split('/')[-1]}"
        if only_fail:
            rs = [r for r in rs if effective_label(r) == "fail"]
        records += rs[:limit] if limit else rs
    console.print(f"{len(records)} runs, variant={variant}, reps={reps}")
    for name in judges.split(","):
        judge = JUDGE_FACTORIES[name]()
        w = 1 if name in ("kev", "kev-strict", "local") else workers

        def progress(i, n, v, judge=judge):
            if v.error:
                console.print(f"  [{i}/{n}] [magenta]ERR[/magenta] {v.case_id} {v.error}")
            elif i % 25 == 0 or i == n:
                console.print(
                    f"  [{i}/{n}] {judge.name} last p={v.pass_prob:.2f} {v.latency_s:.2f}s"
                )

        console.rule(judge.name)
        written = run(
            records, judge, reps, Path(out), variant=variant, workers=w, progress=progress
        )
        console.print(f"{judge.name}: {written} new verdicts")


@calib.command("report")
def calib_report(
    verdicts: str = "data/calibration/verdicts.jsonl",
    runs: str = "data/calibration/runs-gemma4-31b.jsonl,data/calibration/runs-qwen3-0.6b.jsonl",
    out_json: str | None = "data/calibration/report.json",
) -> None:
    """Print the judge comparison table and the injection experiment."""
    from pathlib import Path

    from payerbench.calibration.report import report

    text = report(
        [Path(p) for p in verdicts.split(",")],
        [Path(p) for p in runs.split(",")],
        Path(out_json) if out_json else None,
    )
    console.print(text, markup=False)


if __name__ == "__main__":
    app()
