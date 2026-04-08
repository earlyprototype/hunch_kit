# hunch_kit

Structured experimentation for iterative creative workflows. Test your hunches.

## What It Is

A standalone, provider-agnostic experiment framework with human-in-the-loop evaluation. No external tool dependencies beyond Python. A local web UI for side-by-side comparison and scoring. An MCP server for AI-assisted experiment management.

hunch_kit imposes just enough structure to keep experiments honest — hypothesis declaration, single-variable isolation, baseline lineage — without the overhead of platforms designed for programmatic LLM API pipelines.

## Why It Exists

Existing experiment tracking tools (Promptfoo, Langfuse, Agenta, MLflow) are architecturally oriented toward programmatic LLM API interaction. None provide a lightweight, provider-agnostic framework with native human-in-the-loop evaluation for creative workflows that produce outputs requiring visual or subjective judgement — PDFs, images, slide decks, design compositions.

hunch_kit fills that gap.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  CLI (hunch init / run / eval / list / lineage)         │
├─────────────────────────────────────────────────────────┤
│  MCP Server (tools / resources / prompts)               │
├─────────────────────────────────────────────────────────┤
│  Core Library                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │ manifest │ │  rubric  │ │  runner  │ │ providers │ │
│  └──────────┘ └──────────┘ └──────────┘ └───────────┘ │
├─────────────────────────────────────────────────────────┤
│  Evaluation                                             │
│  ┌──────────────────────┐ ┌───────────────────────────┐ │
│  │ Web UI (FastAPI)     │ │ LLM Judge (hooks)         │ │
│  │ Side-by-side scoring │ │ Pluggable, optional       │ │
│  └──────────────────────┘ └───────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

Three interfaces, one core:
- **CLI** — scaffold, execute, and evaluate experiments from the terminal
- **Web UI** — side-by-side comparison with rubric-based human scoring
- **MCP Server** — AI-assisted experiment planning, rubric construction, and result review via any MCP-compatible client

## Quick Start

### Installation

```bash
pip install -e .
```

For MCP server support:
```bash
pip install -e ".[mcp]"
```

### Create an Experiment

```bash
hunch init
```

You will be prompted for:
- **Experiment ID** — a unique slug (e.g. `ex_001_baseline`)
- **Hypothesis** — what you expect the change to achieve
- **Variable** — the single variable being tested
- **Value** — what the variable was changed to
- **Baseline** — which previous experiment this descends from

### Run an Experiment

```bash
hunch run ex_001_baseline
```

The runner loads the manifest, resolves the provider, executes, and writes results back to the manifest.

### Evaluate

```bash
hunch eval
```

Opens a local web UI at `http://localhost:8888` with:
- Side-by-side comparison of baseline and current outputs
- Scoring form generated from rubric dimensions
- Scores saved directly to the experiment manifest

### View Experiments

```bash
hunch list
hunch lineage
```

## Core Concepts

### Experiments start with hunches

Every experiment declares a hypothesis, a single variable being changed, and the baseline it descends from. The manifest schema enforces this discipline.

### Pluggable providers

A provider is anything that takes an input and produces an output. Providers implement a simple interface: `run(input, config) → result`. A text generator, a slide deck builder, a style evaluator — any input→output workflow can be a provider.

### Human-in-the-loop evaluation

A lightweight local web UI presents experiment outputs side-by-side for human scoring. Evaluators score against rubric dimensions with configurable scales and calibration anchors. Scores are written back to the experiment manifest.

### Experiment lineage

Every experiment declares its parent. Over time this builds a genealogy — a traceable path from initial hunch to validated output. The lineage prevents the ambiguity that plagues ad-hoc iteration.

### Optional LLM-as-judge

For text-evaluable criteria, an LLM can score outputs against rubric assertions. This runs alongside (not instead of) human evaluation. The judge interface is pluggable — bring your own API key and model.

## MCP Server

The MCP server exposes hunch_kit to AI assistants via three capability types:

**Tools** — operations with real side effects:
- `init_experiment` — scaffold experiments from structured inputs
- `create_rubric` — construct rubrics from validated dimension data
- `run_experiment` — execute through a provider
- `score_experiment` — record evaluation scores

**Resources** — read-only experiment data:
- `experiment://list` — all experiments with summaries
- `experiment://lineage` — full genealogy tree
- `rubric://list` — available rubrics
- `rubric://{name}` — specific rubric definition

**Prompts** — guided workflows:
- `experiment_planning` — hypothesis formulation and variable isolation
- `rubric_construction` — dimension definition for a domain
- `experiment_review` — structured result review and next-step planning

### MCP Configuration

```json
{
  "mcpServers": {
    "hunch-kit": {
      "command": "python",
      "args": ["-m", "hunch_kit_mcp"],
      "env": {
        "HUNCH_KIT_WORKSPACE": "/path/to/your/project"
      }
    }
  }
}
```

## Project Structure

```
hunch_kit/
├── hunch_kit/                  # Core Python package
│   ├── cli.py                  # Click-based CLI
│   ├── manifest.py             # Experiment manifest schema + I/O
│   ├── rubric.py               # Evaluation rubric schema + I/O
│   ├── runner.py               # Experiment execution orchestrator
│   ├── providers/
│   │   ├── base.py             # Abstract provider interface
│   │   └── echo.py             # Reference provider for testing
│   └── evaluation/
│       ├── human.py            # FastAPI web UI for scoring
│       └── llm_judge.py        # LLM judge interface (hooks)
├── hunch_kit_mcp/              # MCP server
│   ├── server.py               # Server setup
│   ├── tools.py                # Tools (create, run, score)
│   ├── resources.py            # Resources (experiment data)
│   └── prompts.py              # Prompts (guided workflows)
├── rubrics/                    # Evaluation rubric definitions
├── experiments/                # Experiment runs
├── tests/                      # Test suite
└── pyproject.toml
```

## Writing a Custom Provider

```python
from hunch_kit.providers.base import BaseProvider, ProviderResult

class MyProvider(BaseProvider):
    name = "my_backend"

    def run(self, input_text, config=None):
        # Your generation logic here
        output = do_something(input_text, config)
        return ProviderResult(
            output=output,
            status="success",
            metadata={"model": "v2"},
        )
```

Register with the runner:

```python
from hunch_kit.runner import register_provider
register_provider(MyProvider)
```

## Key Design Decisions

1. **No external evaluation tool dependency** — the evaluation UI and experiment runner are built natively in Python.
2. **Provider-agnostic** — hunch_kit does not assume the backend is an LLM API. It works for any input→output workflow.
3. **Human scoring is first-class** — automated scoring is optional. The web UI for side-by-side evaluation is a core feature, not an afterthought.
4. **Local-first** — everything runs on the user's machine. No cloud dependencies, no accounts, no data leaves the system.
5. **Manifest-driven** — the `experiment.yaml` manifest is the single source of truth for each experiment.

## Licence

MIT
