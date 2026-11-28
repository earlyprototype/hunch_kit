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
hunch --root examples/design_thinking eval dt_v3
```

## Why the title broke, and how one change fixed it

NotebookLM does not *typeset* infographic text — it **paints** it as part of a
generated image, so letters can double, drop, or swap. Going `concise` reduced
the crowded body text (fewer errors) but promoted a large ornate **serif**
headline — the single riskiest place for letterform corruption. Constraining
headings to plain sans-serif with an explicit spelling rule (`dt_v3`) removed
the corruption source. That is the whole point of the tool: a human caught the
flaw, changed **one** thing, and verified the improvement.

## Blind review

From the repository root, start the reviewer on a disposable copy of the campaign.
The UI reads the two checked-in PNGs for `dt_v3` and its baseline, `dt_v2`.
The preview covers the printed provider mark in the lower right corner.
All other pixels in these PNGs match the source. The ordinary scoring page
displays the original images.

In `hunch_project.yaml`, each `blind_image_masks` entry names an experiment,
its verified pixel digest and a rectangle (`left, top, right, bottom`).
The digest is SHA-256 over the width and height as two unsigned 32-bit
big-endian integers followed by the oriented sRGB RGBA bytes in row order.
Lossless re-exports with the same pixels use the same mask. If a designated
image changes, verify its pixels and rectangle before updating the entry.
Older configurations keyed by encoded-file SHA-256 require an exact match
for every image on the ballot.

```bash
demo=$(mktemp -d)
cp -R examples/design_thinking "$demo/campaign"
hunch --root "$demo/campaign" eval --blind dt_v3
```

Open `http://127.0.0.1:8888/`. Each new review assigns the images to A and B
at random. Refreshing the comparison URL keeps that assignment while the
server is running. Select **A**, **B** or **Tie** to record the choice and reveal
both experiment IDs. Choosing the child records `better`, choosing the baseline
records `worse`, and a tie records `equivalent` in the child's
`experiment.yaml`. The baseline manifest stays byte-identical.

Blind review accepts still PNG, JPEG, GIF and WebP images. It applies embedded
color profiles before removing metadata. For UTF-8 TXT, Markdown, YAML and JSON
outputs, the preview omits a leading provider tag such as `[stub_local]`.
If the text still names its provider, the ballot is unavailable.

Voting pauses while either experiment is running. Local runners and voters
under the same user account share locks in `~/.hunch-kit-locks`. The baseline
can be read-only. If an output or the child's preference changes before
submission, start a new review. Edits to notes are preserved. After a vote,
the comparison URL shows the choice and mapping for the rest of the session.

The ordinary scoring form checks its revision on save. Reload a stale form
to see the latest preference. Score clients that omit the revision can still
update individual scores.

The ordinary experiment list also offers **Blind comparison** for children.
For the root's rubric and scores, open `/eval/dt_v1`.

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
