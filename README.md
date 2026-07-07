# hunch_kit

You changed three things at once. The output got worse. Now you can't tell which change broke it.

**hunch_kit** is a local-first experiment framework for creative workflows where you need to *isolate variables*, *track lineage*, and *score outputs that require human eyes* — slide decks, generated images, prompt compositions, PDFs, design systems.

One hunch. One variable. One comparison. Repeat until you actually know what works.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/licence-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/earlyprototype/hunch_kit/actions/workflows/tests.yml/badge.svg)](https://github.com/earlyprototype/hunch_kit/actions/workflows/tests.yml)

---

## 30-Second Demo

<p align="center">
  <img src="assets/hunch_kit.gif" alt="Two slide designs shown side by side in the hunch_kit evaluation UI — a plain draft versus a polished redesign — scored by hand with a quality slider and a preference." width="100%">
</p>

<p align="center"><em>Change one variable, see both outputs side by side, and score by hand. Here: two AI-generated album-cover concepts, judged in the eval UI.</em></p>

```bash
# Scaffold a campaign
hunch init-project ./my_campaign --name "slide_styles"
cd my_campaign

# Create your baseline experiment
hunch init --id ex_001_baseline \
    --hypothesis "Default styling produces acceptable slides" \
    --variable "style_template" --value "default"

# Drop your input file into the experiment directory, then run it
hunch run ex_001_baseline

# Branch: test a hunch
hunch init --id ex_002_dark_mode \
    --hypothesis "Dark mode improves visual hierarchy" \
    --variable "style_template" --value "dark_professional" \
    --baseline ex_001_baseline

hunch run ex_002_dark_mode

# Open the evaluation UI — compare side-by-side, score with your rubric
hunch eval

# See the family tree
hunch lineage
# root
# └── ex_001_baseline
#     └── ex_002_dark_mode
```

---

## Why This Exists

Existing experiment tracking tools — Promptfoo, Langfuse, Agenta, MLflow — are built for programmatic LLM API pipelines. They assume your outputs are text strings you can diff, and your evaluation is automated scoring against assertions.

That doesn't work when:
- **Your output is visual** — a PDF, a slide deck, an image, a design composition
- **Your evaluation is subjective** — "does this *feel* better?" requires human eyes
- **Your backend isn't an LLM API** — it's a rendering pipeline, a style system, a build tool

hunch_kit fills that gap: structured experimentation with human-in-the-loop evaluation, for any input→output workflow.

---

## Installation

```bash
pip install -e .
```

With MCP server support (for AI-assisted experiment planning):

```bash
pip install -e ".[mcp]"
```

---

## Core Concepts

### Experiments start with hypotheses

Every experiment declares **one hypothesis**, **one variable being changed**, and its **baseline** (parent). The manifest schema enforces single-variable isolation — no more "I changed the font *and* the colours *and* the spacing."

### Pluggable providers

A provider is anything that takes an input and produces an output. Text generators, slide deck builders, image pipelines, style evaluators — implement `run(input, config) → result` and you're done.

### Human-first evaluation

A local web UI presents outputs side-by-side for scoring against rubric dimensions. Configurable scales with calibration anchors ("1 = Unusable, 5 = Acceptable, 10 = Exceptional"). Scores write directly back to the experiment manifest.

### Experiment lineage

Every experiment knows its parent. Over time this builds a genealogy — a traceable path from initial hunch to validated output. No more "wait, which version was the good one?"

### Local-first

No cloud. No accounts. No data leaves your machine. Everything is YAML files on disk.

---

## Campaign Structure

Work is organised in **campaigns** — a directory containing `hunch_project.yaml` at the root:

```
my_campaign/
├── hunch_project.yaml          # Campaign config (defaults, provider, rubric)
├── experiments/
│   ├── ex_001_baseline/
│   │   ├── experiment.yaml     # ← the manifest (single source of truth)
│   │   ├── input.txt           # your input
│   │   └── output/             # provider output
│   └── ex_002_dark_mode/
│       ├── experiment.yaml
│       └── output/
├── rubrics/
│   └── slide_quality.yaml      # scoring dimensions + anchors
└── providers/                  # optional campaign-local providers
    └── my_backend.py           # auto-discovered, overrides built-ins
```

**Campaign resolution:** the CLI and MCP server find your campaign root by:
1. `--root /path/to/campaign` (explicit)
2. `HUNCH_KIT_WORKSPACE` environment variable
3. Walk upward from `cwd` looking for `hunch_project.yaml`
4. Fall back to current working directory

A reference layout lives in `examples/campaign/`.

---

## Writing a Provider

```python
from hunch_kit.providers.base import BaseProvider, ProviderResult

class MyProvider(BaseProvider):
    name = "my_backend"

    def run(self, input_text, config=None):
        output = your_pipeline(input_text, config)
        return ProviderResult(
            output=output,
            status="success",
            metadata={"model": "v2"},
        )
```

**Campaign-local:** drop the file in `<campaign>/providers/`. It's discovered by `name` at runtime and overrides any built-in with the same name.

**Programmatic:** register directly in application code:

```python
from hunch_kit.runner import register_provider
register_provider(MyProvider)
```

---

## MCP Server

hunch_kit ships an MCP server for AI-assisted experiment management. Three capability types:

| Type | Description |
|------|-------------|
| **Tools** | `init_experiment`, `create_rubric`, `run_experiment`, `score_experiment` |
| **Resources** | `experiment://list`, `experiment://lineage`, `rubric://list`, `rubric://{name}` |
| **Prompts** | `experiment_planning`, `rubric_construction`, `experiment_review` |

### Configuration

```json
{
  "mcpServers": {
    "hunch-kit": {
      "command": "python",
      "args": ["-m", "hunch_kit_mcp"],
      "env": {
        "HUNCH_KIT_WORKSPACE": "/path/to/your/campaign"
      }
    }
  }
}
```

---

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
│  │ Web UI (FastAPI)     │ │ LLM Judge (optional)      │ │
│  │ Side-by-side scoring │ │ Pluggable, bring your key │ │
│  └──────────────────────┘ └───────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

---

## Design Decisions

1. **No external evaluation dependency** — the evaluation UI and runner are built natively in Python. No Node.js, no Promptfoo, no managed service.
2. **Provider-agnostic** — hunch_kit doesn't assume LLMs. Any input→output workflow can be a provider.
3. **Human scoring is first-class** — automated scoring is optional. The web UI is a core feature, not an afterthought.
4. **Local-first** — everything runs on your machine. No cloud, no accounts, no telemetry.
5. **Manifest-driven** — `experiment.yaml` is the single source of truth for each experiment.
6. **Campaign-scoped** — rubrics, experiments, and providers live under one root. Multiple studies never collide.

---

## Licence

MIT — [earlyprototype](https://github.com/earlyprototype)
