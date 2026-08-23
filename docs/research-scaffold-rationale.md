# Why the experiment scaffold is decision-grade

The scaffold is intentionally a protocol rather than a generated claim. It converts a research opportunity into a falsifiable product decision.

## Structure

1. Product decision: should the intervention be adopted or should the baseline remain?
2. Hypothesis and mechanism: name one intervention and explain the expected user-visible effect.
3. Comparison contract: hold workflow, permissions, timeout, fixed cases, evaluator, and safety constraints constant.
4. Metric contract: task success, evidence grounding, memory safety, and tool efficiency are present for every case.
5. Decision gates: task success improves by at least 0.05; grounding and memory safety do not regress.
6. Ablations and risks: remove the intervention; stress stale memory, insufficient evidence, tool-call inflation, and unsafe writes.
7. Next decision: pass moves to a limited product experiment; failure revises the hypothesis rather than hiding the result.

## State semantics

- draft: a hypothesis exists but no protocol folder exists.
- scaffolded: the protocol exists, but implementations are missing or evaluation is inconclusive.
- building: an implementation agent is working on the baseline and candidate.
- built: every fixed case and metric is present and the decision gates pass.
- failed: implementation or evaluation failed, or the candidate failed a gate.

## Design influences

The structure reflects three durable agent-engineering principles: preserve traceable observability, make tools and their feedback explicit, and evaluate agents in execution environments rather than with a single prose score.

- OpenAI, New tools for building agents: https://openai.com/index/new-tools-for-building-agents/
- Anthropic, Building effective agents: https://www.anthropic.com/engineering/building-effective-agents
- Anthropic, Demystifying evals for AI agents: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
