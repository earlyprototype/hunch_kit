# Example: Design Thinking (a real incremental lineage)

This campaign is the one shown in the demo GIF. It is a **worked example of the
core method** — hold everything constant, change one variable, record it, and
judge whether it helped.

The outputs are infographics generated with [NotebookLM](https://notebooklm.google.com)
under the **FUJI method** (a YAML "style spec" passed as the generation prompt;
the *content* comes from the notebook source, the *style* from the spec).

## The lineage

| Experiment | Variable changed | Result | text_accuracy | visual_fidelity |
|---|---|---|---|---|
| `dt_v1` (baseline) | — (standard detail) | Strong style, but dense small text is garbled by the renderer ("cntangled", "tanglole") | 3 | 9 |
| `dt_v2` | `detail`: standard → **concise** | Body text cleans up — but a large ornate serif title gets garbled → "BUILLD" | 6 | 9 |
| `dt_v3` | `heading_typography`: serif mix → **plain sans + spelling rule** | All text correct, concise body kept; headings plainer | 10 | 8 |

Each `experiment.yaml` records the single changed variable; each `input.txt`
holds the exact fuji prompt used. Open the eval UI to see baseline-vs-current
side by side with the prompt and the hand scores:

```bash
hunch eval --project examples/design_thinking dt_v3
```

## Why the title broke, and how one change fixed it

NotebookLM does not *typeset* infographic text — it **paints** it as part of a
generated image, so letters can double, drop, or swap. Going `concise` reduced
the crowded body text (fewer errors) but promoted a large ornate **serif**
headline — the single riskiest place for letterform corruption. Constraining
headings to plain sans-serif with an explicit spelling rule (`dt_v3`) removed
the corruption source. That is the whole point of the tool: a human caught the
flaw, changed **one** thing, and verified the improvement.

## Reproducing (NotebookLM)

The outputs were generated with the `notebooklm` CLI ([notebooklm-py](https://github.com/teng-lin/notebooklm-py)):

```bash
notebooklm create "Design Thinking"
notebooklm source add design_thinking_source.md -n <notebook_id>
# style spec (the fuji YAML) is passed as the instruction; detail is the variable
notebooklm generate infographic "$(cat tech_neon_art_concise.yaml)" \
  -n <notebook_id> --orientation portrait --detail concise
notebooklm download infographic result.png -n <notebook_id>
```

`provider: notebooklm` in each manifest denotes an externally generated output —
there is no local provider script to run.
