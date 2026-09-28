# Agent Engineering Roadmap — Sept 2026

Research-driven project plan for becoming a top-tier applicant in agent build/eval roles.
Sources: LangChain State of Agent Engineering (1,340 respondents), LangChain/Braintrust/DeepEval/Pydantic Jev docs,
VentureBeat + Octomind Jev security findings, jevals README, OTel GenAI semconv (v1.41), Inspect Petri v3 README,
Databricks DAIS 2026 Agent Bricks post, Future AGI CI-gate guide, Cipher Projects LangSmith/Phoenix/Braintrust
comparison, HN "Who is using MCP in production?", r/LocalLLaMA + openclawdc M5 Ultra coverage, WWDC26 MLX guide.

---

## 1. What the market actually looks like (Sept 2026)

| Signal | Number | Implication for you |
|---|---|---|
| Teams with agent observability | 89% (94% of prod teams) | Table stakes. Everyone has traces. |
| Teams running offline evals | 52.4% | **The gap.** Evals are the differentiator, not tracing. |
| Teams running online evals | 37.3% | Even rarer. Online eval + alerting is a standout skill. |
| #1 barrier to production | Quality (32%) | Eval/quality engineering is the hiring pain. |
| #2 barrier at 2k+ enterprises | Security (24.9%) | Adversarial evals + MCP security are enterprise-relevant. |
| Enterprises whose agent passed evals then failed in prod | 79 of 157 | Judge calibration and prod-trace replay are unsolved. |
| Enterprises that fully trust automated evals | 8 of 157 | Same. |
| Judge method | Human review 59.8%, LLM-judge 53.3% | Cheap calibrated judges (Jev-class) are the new frontier. |
| Self-hosting models | ~1/3 | Local inference is a real production skill, not a hobby. |

**Tooling consensus**
- Tracing/evals: LangSmith (LangGraph-native, $39/seat, self-host = Enterprise only), Langfuse (MIT, self-host = Postgres+ClickHouse+Redis+S3, acquired by ClickHouse), Arize Phoenix (OpenInference/OTel-native, OSS), Braintrust (CI gates as "a defining use case", closed), MLflow 3 (your work stack; Databricks stores traces in the Lakehouse).
- Standard: **OpenTelemetry GenAI semantic conventions** (still "Development" status at v1.41; `gen_ai.operation.name` = `chat|invoke_agent|execute_tool|invoke_workflow`, `gen_ai.tool.name`, `gen_ai.usage.*`, `gen_ai.evaluation.result` event, MCP conventions `mcp.method.name`/`mcp.session.id`). Vendor-neutral instrumentation is the skill that survives vendor churn.
- **Jev** (TypeSafe AI): a "System One" decision model. Not an LLM; takes `state` + typed questions (Noul = P(true), Choice, Score) and returns probabilities + confidence. $0.042/M input tokens, free output, ~100–500 ms. Shipped as a LangSmith Evals judge Sept 21; integrated in Braintrust, DeepEval (`hybrid`/`system_one` modes), Pydantic AI (`typesafe:jev-latest`), Langfuse, Vercel AI Gateway. LangChain's own test: Jev matched a human oracle on 500/500 binary decisions vs 80% for a frontier LLM judge, at $0.34 vs $28.17. Known weakness: adversarial text in `state` moves the answer (Octomind: block-probability fell 0.76 → 0.48 after a fake "pre-approved" tool output). Rule everyone now repeats: **"don't let the classifier become the authorizer."**
- Local Jev-alikes: **Kev** (`jaredpalmer/kev-4b`, self-hosted, runs on a 32 GB Mac) and **Laya** (ModernBERT, ~10 ms in-process on Apple Silicon). Your 256 GB machine can run all of these plus the agent under test plus a simulated user.
- Red teaming: promptfoo (YAML red-team, acquired by OpenAI Mar 2026), PyRIT (Microsoft, multi-turn), garak (probe scanner), DeepTeam, Inspect AI + **Inspect Petri v3** (Anthropic → Meridian Labs/UK AISI; auditor/target/judge agents, 170+ seeds, 38 dimensions, supports a custom target driver for your own LangGraph scaffolding), Petri Bloom for single-behavior suites.
- Simulated users: **τ²-bench** (Sierra; agent + simulated user modify shared world state; pass^k), Terminal-Bench 2.0.
- MCP "won" (97M monthly SDK downloads, Linux Foundation) but is a security mess: MCPTox 84.2% tool-poisoning success with auto-approve; Endor Labs: 82% of 2,614 servers path-traversal prone. HN consensus: MCP for non-technical end users / OAuth / voice agents; CLIs and skills for developers.
- CI gates done right (Future AGI guide): deterministic checks + sub-50 ms local classifiers resolve 70–85% of cases, frontier judge only on flagged 30–60 examples; absolute floors AND a delta gate using **Welch's t-test** (p < 0.05 and |Δ| > 0.03) or paired bootstrap; rolling 7-day nightly baseline committed as JSON; typed exit codes (2 = block, 6 = retry, 7 = shard).
- Healthcare observability asks: BAA, PHI redaction before storage, immutable audit trail, self-host/VPC, clinician annotation queues, demographic bias slicing. Datadog signs BAAs; Langfuse/Phoenix self-host; OTel semconv's "external storage + reference URL" content mode exists for exactly this.
- Databricks (DAIS 2026): any harness deployable to Databricks Apps; MCP in Unity Catalog; Unity AI Gateway budgets/routing; traces in Lakehouse + LakeWatch PII alerts; SQL "Contextual Policies" for stateful agent authorization. Your work stack is converging on the same problems.

**Where your resume already lands:** LangGraph multi-agent in prod, MLflow tracing + human feedback, conversational evals with archetypes, adversarial evals with LLM-judge, pass-rate regression gate, Unity AI Gateway governance, DAB/GitHub Actions CI/CD. That is more than most applicants.

**Gaps a hiring manager for an agent-eval role would probe:**
1. Vendor-neutral OTel GenAI instrumentation (you list LangSmith + MLflow; no Langfuse/Phoenix/OTel).
2. Judge **calibration** as a measured thing (agreement, repeatability, Brier/ECE/AUROC), not just "LLM-as-judge".
3. System-One / typed-decision judges (Jev, Kev, Laya) and where they break.
4. Named red-team frameworks (promptfoo, PyRIT, Petri) and attack taxonomies (indirect injection, tool poisoning).
5. Simulated-user benchmarks (τ²-bench style) as a public artifact.
6. Statistically sound CI gates (t-test/bootstrap) rather than a raw pass-rate threshold.
7. Local inference + fine-tuning (MLX LoRA), serving, and benchmarking.
8. Public proof: repos, write-ups, OSS contributions. sodabubbles has the projects but no eval/observability story.

---

## 2. One shared substrate: the "PayerBench" reference agent

Build one synthetic health-plan **member-services agent** once, and reuse it in every project below. It gives every artifact a healthcare angle nobody else in the applicant pool has.

- Domain: eligibility lookup, claims status, prior-auth status/rules, benefits FAQ over a fake plan document, provider search. Fully synthetic members with fake-but-realistic PHI so redaction is testable.
- Framework: LangGraph (your strength) with tools exposed both as Python functions and as an MCP server (so MCP security tests are possible).
- Policy doc + world state DB (SQLite/Postgres) so a τ²-style simulator can check outcome, not just wording.
- Model-agnostic via LiteLLM: local (`mlx_lm.server` / `llama-server` on :8080) or cloud (Anthropic, OpenAI) with one env var.
- Lives in this repo under `agent/`.

---

## 3. Projects, ordered from most production-relevant to most fun

### P1. Vendor-neutral Agent Observability Platform (this repo) — weeks 1–3
Goal: one OTel pipeline, four backends, one write-up comparing them. Directly fills gap #1 and finishes Phase 5 of your Mac plan.
- `docker compose`: Langfuse (Postgres + ClickHouse + Redis + MinIO), Arize Phoenix, OTel Collector, grafana/otel-lgtm (Tempo/Loki/Prometheus/Grafana).
- Instrument PayerBench with OTel GenAI semconv (`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`), LangGraph via OpenInference/`openllmetry`, plus MCP spans. Fan out from the Collector to Langfuse, Phoenix, Tempo, and **LangSmith cloud** (OTLP endpoint) simultaneously. Same trace, four UIs. This is how you learn LangSmith deeply while staying vendor-neutral.
- PHI handling: Collector processor running Presidio redaction; use the semconv "external storage + reference URL" content mode for raw messages. Document the HIPAA rationale.
- Route developer-tool telemetry (IDE agents, CLIs) into the same stack; anything that emits OTLP works.
- Deliverable: repo + post "Same agent, four observability backends: what each one loses."

### P2. Judge Calibration Study: LLM vs Jev vs local classifier — weeks 3–5
Goal: the most differentiating artifact on this list. Fills gaps #2 and #3. Nobody has public calibration numbers for Jev-class judges on domain data yet.
- Run PayerBench on ~200 scenarios; hand-label binary pass/fail (LangChain guidance: binary beats 1–5; start with ≥20 labels, aim for 200).
- Judges: frontier LLM judges (Anthropic, OpenAI) via LangSmith Evals and `openevals`; Jev via LangSmith Evals, `jevals`, and Pydantic AI `TypeSafeModel`; **Kev-4b and Laya running locally on the Mac**; DeepEval in `hybrid` and `system_one` modes.
- Measure: oracle agreement, repeatability over 100 reps (LangChain's "signal value" = agreement × repeatability), Brier/ECE/AUROC (`jevals calibrate`), cost, p50/p95 latency.
- Reproduce the Octomind injection: put a fake "pre-approved" field in a tool result and show how each judge's verdict moves; then show the fix (exclude tool output from judge `state`).
- Stretch: distill the human labels into a **Qwen3-4B LoRA classifier with MLX** and add it to the table. That is the "System One" trend in your own hands.
- Deliverable: post + dataset. Consider a PR to `openlayer-ai/jevals` (alpha, 3 commits, wide-open contribution surface) adding a healthcare `PHI`/`PriorAuthPolicy` eval.

### P3. Adversarial Regression Suite for agents — weeks 5–8
Goal: fills gap #4 and maps to the enterprise "security is #2" signal.
- promptfoo red-team YAML (injection, PHI exfiltration, jailbreak, excessive agency), PyRIT multi-turn orchestrations, garak probes against the model endpoint.
- **Inspect Petri v3 with a custom target driver** wrapping your LangGraph agent (`target_tools="fixed"` with your real tool schemas), plus domain seeds: prior-auth sycophancy ("just approve it"), PHI leakage to the wrong member, indirect injection through a claims-note tool result, MCP tool poisoning via a malicious server description. Start with `--limit 5`; a full run is hours and real API spend.
- Defense layer to measure before/after: deterministic checks + `jevals` `IndirectInjection`/`PHI` gates + human approval on irreversible tools. Report attack success rate per category.
- Wire into GitHub Actions nightly; store results as Langfuse datasets.
- Deliverable: post "Red-teaming a healthcare agent with promptfoo, PyRIT and Petri" and a reusable seed pack.

### P4. PayerBench: a τ²-bench-style simulated-user benchmark — weeks 8–12
Goal: turns your work-only "five analyst archetypes" practice into a public benchmark. Fills gap #5 and is FDE-interview gold.
- Domain policy doc, tool set, DB state, 50–100 tasks with expected end-state; user personas (confused, adversarial, elderly, non-English-primary, provider office).
- Simulated user runs on a local model (Qwen3-235B or GLM-4.7 on the Mac); agent under test = local or cloud; judge = P2 winner. Report pass^k.
- Leaderboard of local vs cloud models published on sodabubbles.ai; dataset on Hugging Face.

### P5. Reusable statistical CI eval gate (cross-cutting, built alongside P1–P4)
Goal: fills gap #6; upgrades what you already do at work.
- A composite GitHub Action: runs the golden set (60% happy path / 20% edge / 10% refusal / 10% historical failures), absolute floors + Welch's t-test or paired bootstrap delta gate, rolling nightly baseline JSON, typed exit codes, PR comment with the table. Classifier cascade so frontier-judge spend is cents per PR.
- **Databricks bridge:** the same gate implemented as `mlflow.genai` custom scorers, including one that calls Jev. Deployable via Asset Bundles. Directly usable at work, and it lines up with the Databricks Generative AI Engineer Associate cert your resume already flags as "next."

### P6. Mac Studio inference bench and serving layer — weeks 1–2, then ongoing
Goal: gap #7; also the engine under P1–P4.
- Runtimes: `mlx_lm.server`, `llama-server` (with MTP draft heads: `--spec-type draft-mtp --spec-draft-n-max 2`), vLLM-mlx. Models: gpt-oss-120B Q8 (~120 GB, cleanest tool-call JSON per reports), Qwen3-VL-235B Q4 (~130 GB), GLM-4.7 358B Q3 (~155 GB), DeepSeek V4 Flash Q4 (~80 GB), Gemma 4 26B-A4B (~17 GB, fast multimodal). Reserve 15–20% of RAM for KV cache; raise `iogpu.wired_limit_mb` as your plan notes.
- Measure decode tok/s, TTFT, and **tool-call JSON validity rate** with 1–3k-token system prompts. Independent M5 Ultra numbers barely exist yet; yours would be cited.
- Front everything with LiteLLM exporting OTel to P1.
- Deliverable: post + table on sodabubbles.

### P7. Instrument the existing sodabubbles bots — weekend project each
- Media-server agent, tax RAG Discord bot, AI digest: add OTel spans, ship to the Mac's Langfuse via Cloudflare Tunnel, add golden sets, put a public **"agent quality" page** on sodabubbles.ai showing live pass rates and cost per conversation. Converts hobby demos into observability proof.
- Tax RAG: replace/compare Ragas with DeepEval hybrid mode and `jevals` (jevals reports $0.03 vs $2.60 per 1k samples vs Ragas + gpt-4.1-mini). Add a PII guard.
- Job-application agent: put Jev `Choice` gates on apply/skip decisions with human approval on the irreversible step. Dogfoods "classifier is not the authorizer."

### P8. Fun: Local Agent Arena on sodabubbles.ai
- Local models compete on Terminal-Bench-style and PayerBench tasks nightly on the Mac; sodabubbles shows a live leaderboard with traces you can click into (Phoenix embed or exported HTML). Pure fun, but it exercises P4, P6 and P1 daily.

### P9. Fun: Pokemon scanner, local edition
- Run Qwen3-VL-235B locally against the cloud vision model; eval identification accuracy and pricing correctness with a small labeled set. Shows multimodal eval, which almost nobody has.

---

## 4. Suggested 12-week sequence

| Weeks | Focus | Output |
|---|---|---|
| 1–2 | P6 serving + P1 compose stack + PayerBench agent skeleton | Local models serving; traces in four backends |
| 3–5 | P2 calibration study | Post #1 + dataset; jevals PR |
| 5–8 | P3 red-team suite; P5 gate v1 | Post #2; nightly Actions |
| 8–12 | P4 benchmark; P5 MLflow bridge; P7 sodabubbles instrumentation | Post #3; HF dataset; public quality page |
| ongoing | Databricks GenAI Engineer Associate cert; one OSS PR per month (jevals, Langfuse, promptfoo, inspect_petri) | Resume lines |

Each post is short and number-heavy: a table, a chart, a repo link. Three posts plus a benchmark plus a dashboard is a stronger portfolio than ten unlinked demos.

---

## 5. Cost and account notes
- Jev: $0.042/M input, free output; waitlist removed Sept 20 with $5 free credit; also reachable via Vercel AI Gateway. Pin `jev-1.13.0` in experiments; `jev-latest` moves.
- LangSmith: Plus $39/seat/mo; free tier is enough for P1 tracing + Evals experiments. Traces cost ~$0.50/1k after the base allotment.
- Langfuse/Phoenix/OTel stack: free, self-hosted on the Mac.
- Petri full run: hours and real judge spend; always `--limit` first, use a local target where possible.
- Frontier judges: cap with the classifier cascade in P5; budget ~$20–30 per full 200-example run without it.

## 6. Resume lines this unlocks (draft)
- Built a vendor-neutral OTel GenAI observability platform (Langfuse, Arize Phoenix, LangSmith, Grafana Tempo) with PHI redaction and MCP tracing.
- Published a judge-calibration study (LLM vs System-One vs locally fine-tuned classifier) reporting agreement, repeatability, Brier/ECE across 200 labeled agent traces.
- Authored a red-team regression suite (promptfoo, PyRIT, Inspect Petri) for a healthcare agent; measured and reduced attack success rate across injection, PHI exfiltration and tool-poisoning categories.
- Released PayerBench, a τ²-bench-style simulated-user benchmark for health-plan agents, with a public leaderboard.
- Shipped a statistical CI eval gate (Welch's t-test, bootstrap CIs) as a reusable GitHub Action and MLflow 3 scorers.
