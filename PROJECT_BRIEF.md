# hunch_kit

Structured experimentation for iterative creative workflows. Test your hunches.

## What It Is

A standalone, provider-agnostic experiment framework with human-in-the-loop evaluation. No external tool dependencies beyond Python and optionally an LLM API for automated scoring. A local web UI for side-by-side comparison and scoring.

## Origin

Born from a real problem: iterating on YAML-based style templates for NotebookLM slide generation. Collaborating with a frontier LLM on an iterative improvement programme broke down as context degraded, variables became entangled, and tracking fell apart. The ad-hoc folder-per-test approach with manual changelogs could not sustain systematic experimentation.

A task-specific tool (_promptForge) was built first, wrapping the NotebookLM MCP pipeline via Promptfoo. hunch_kit generalises this into a standalone framework that replaces the Promptfoo dependency with native capabilities.

## Architecture (carried from _promptForge, generalised)

```
hunch_kit/
├── hunch_kit/                     # Python package
│   ├── cli.py                     # CLI entry point
│   ├── project.py                 # Campaign config + root discovery
│   ├── manifest.py                # Experiment manifest schema + I/O
│   ├── runner.py                  # Experiment execution orchestrator
│   ├── evaluation/
│   │   ├── human.py               # Local web UI for side-by-side scoring
│   │   └── llm_judge.py           # Optional LLM-as-judge scoring
│   └── providers/
│       ├── base.py                # Provider interface (abstract)
│       └── echo.py                # Reference provider for testing
├── examples/campaign/             # Reference campaign (rubrics, experiments, providers)
├── pyproject.toml
└── README.md
```

## Core Concepts

### Experiments start with hunches
Every experiment declares a hypothesis (a formalised hunch), a single variable being changed, and a baseline it descends from. The manifest schema enforces this discipline.

### Pluggable providers
A provider is anything that takes an input (prompt, config, style spec) and produces an output. Providers implement a simple interface: `run(input, config) → result`. NotebookLM slide generation, Gemini text generation, a Figma plugin — any backend can be a provider.

### Human-in-the-loop evaluation
A lightweight local web UI presents experiment outputs side-by-side for human scoring. Evaluators score against rubric dimensions (configurable per domain). Scores are written back to the experiment manifest.

### Optional LLM-as-judge
For text-evaluable criteria, an LLM can score outputs against rubric assertions. This runs alongside (not instead of) human evaluation. Requires the user's own API key.

### Experiment lineage
Every experiment declares its parent. Over time this builds a genealogy — a traceable path from initial hunch to production-quality output. The lineage prevents the ambiguity that plagued the original ad-hoc process.

## Key Design Decisions

1. **No Promptfoo dependency** — the evaluation UI and experiment runner are built natively in Python, eliminating the Node.js dependency and the opacity of wrapping someone else's tool.

2. **Provider-agnostic** — hunch_kit does not assume the backend is an LLM API. It works for any input→output workflow: prompt templates, config files, style specs, source document combinations.

3. **Human scoring is first-class** — automated scoring is optional. The web UI for side-by-side evaluation is a core feature, not an afterthought. This acknowledges that many creative workflows produce outputs (PDFs, images, visual designs) that cannot be meaningfully scored by an LLM.

4. **Local-first** — everything runs on the user's machine. No cloud dependencies, no accounts, no data leaves the system.

5. **Manifest-driven** — the experiment.yaml manifest is the single source of truth. It captures hypothesis, variables, lineage, automated scores, and human scores in one place.

## Relationship to _promptForge

_promptForge remains as a working task-specific tool for NotebookLM slide experiments. hunch_kit is the generalised framework. A NotebookLM provider for hunch_kit can be written to replicate _promptForge's functionality within the new architecture.

## Reference Implementation

See `_tools/_promptForge/` for the task-specific predecessor:
- `manifests/_template.yaml` — experiment manifest schema (carries over directly)
- `providers/notebooklm.py` — example of a custom provider wrapping an async API
- `rubrics/slide_quality.yaml` — example of domain-specific evaluation criteria
- `scripts/init_experiment.py` — experiment scaffolding pattern

## Market Context

A landscape assessment (see `_prompt_experiments/TOOLING_LANDSCAPE_REPORT.md`) found that existing tools (Promptfoo, Langfuse, Agenta, MLflow) are architecturally oriented toward programmatic LLM API interaction. None provide a lightweight, provider-agnostic framework with native human-in-the-loop evaluation for creative workflows. hunch_kit fills this gap.

## Target

GitHub portfolio piece at github.com/earlyprototype/hunch_kit
