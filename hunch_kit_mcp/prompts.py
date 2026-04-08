"""MCP Prompts — workflow guidance for LLM-assisted experimentation.

Prompts inject domain knowledge and process structure into the
conversation. They guide the LLM through experiment planning,
rubric construction, and result review without performing any
operations themselves.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    """Register all hunch_kit prompts with the MCP server."""

    @mcp.prompt()
    def experiment_planning() -> str:
        """Guide the user through formulating an experiment hypothesis.

        Walks through variable isolation, baseline selection, and
        hypothesis formulation to ensure experiments are well-structured.
        """
        return """# Experiment Planning

Help the user design a well-structured experiment by working through
the following steps. Ask one question at a time and wait for answers.

## Step 1: Identify the Goal
Ask: "What are you trying to improve or understand?"

## Step 2: Isolate the Variable
Ask: "What single change do you want to test?"

Important: each experiment should change exactly one variable from
the baseline. If the user describes multiple changes, help them
separate these into distinct experiments.

## Step 3: Select the Baseline
Ask: "Which existing experiment (or starting point) should this
be compared against?"

Use the experiment://list resource to show available experiments.
If this is the first experiment, the baseline can be empty.

## Step 4: Formulate the Hypothesis
Help the user write a clear, falsifiable hypothesis in the form:
"Changing [variable] to [value] will [expected outcome] because [reasoning]."

## Step 5: Choose a Rubric
Ask: "How will you know if the change worked? What dimensions matter?"

Use the rubric://list resource to show available rubrics. If none
fit, offer to create a new one using the create_rubric tool.

## Step 6: Create the Experiment
Once all inputs are gathered, use the init_experiment tool with
the structured data to scaffold the experiment.

Confirm each field with the user before creating:
- ID (suggest a descriptive slug)
- Hypothesis
- Variable changed
- Variable value
- Baseline
- Provider
- Rubric"""

    @mcp.prompt()
    def rubric_construction(domain: str = "") -> str:
        """Guide the user through defining evaluation dimensions for a domain.

        Args:
            domain: Optional domain hint (e.g. 'slide design', 'text generation')
        """
        domain_hint = f' for "{domain}"' if domain else ""
        return f"""# Rubric Construction{domain_hint}

Help the user define evaluation dimensions by working through these
questions. Each dimension needs a name, description, scale, evaluator
type, and optional score anchors.

## Step 1: Identify What Matters
Ask: "What qualities make a good output in your domain? List the
3-5 most important aspects."

## Step 2: Define Each Dimension
For each quality identified, gather:
- **Name**: A short identifier (e.g. 'visual_clarity', 'content_accuracy')
- **Description**: One sentence explaining what this measures
- **Scale**: Min and max score (default 1-10)
- **Evaluator**: Who scores this?
  - "human" — requires visual/subjective judgement
  - "llm" — can be scored from text output alone
  - "both" — benefits from both perspectives
- **Anchors**: What does a low score look like? A middle score? A high score?

## Step 3: Calibrate
For each dimension, ask the user to describe:
- What a score of 1 looks like (the worst case)
- What a score of 5 looks like (acceptable but unremarkable)
- What a score of 10 looks like (the ideal outcome)

These become the anchors dictionary.

## Step 4: Create the Rubric
Once all dimensions are defined, use the create_rubric tool with
the structured data. Confirm the complete rubric with the user
before creating it.

Present a summary table:
| Dimension | Evaluator | Scale | Anchor (low) | Anchor (high) |
before calling the tool."""

    @mcp.prompt()
    def experiment_review() -> str:
        """Guide the user through reviewing experiment results and planning next steps.

        Structures the review around what worked, what surprised,
        and what to test next.
        """
        return """# Experiment Review

Help the user review their experiment results and decide on next steps.

## Step 1: Load Context
Use experiment://list to see all experiments and their status.
Ask which experiment(s) the user wants to review.

Load the specific experiment(s) using experiment://current/{id}.

## Step 2: Review Results
Walk through:
1. **Status**: Did the experiment succeed? Any errors?
2. **Scores**: What were the human scores? How do they compare
   to the baseline?
3. **Preference**: Was the output better, worse, or equivalent?
4. **Surprises**: Did anything unexpected happen?

## Step 3: Interpret
Help the user interpret results:
- Was the hypothesis supported or refuted?
- Which dimensions improved? Which degraded?
- Were there trade-offs between dimensions?

## Step 4: Plan Next
Based on the results, help the user decide:
- **If better**: Should this become the new baseline? What variable
  should be tested next?
- **If worse**: Was the variable change too aggressive? Should a
  smaller change be tested?
- **If equivalent**: Is the variable irrelevant, or was the change
  too subtle to detect?

## Step 5: Create Follow-up
If the user wants to continue, use experiment_planning to design
the next experiment in the lineage.

Use experiment://lineage to show the current genealogy and where
the new experiment would fit."""
