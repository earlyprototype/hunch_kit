# Example campaign layout

This directory shows the **form** of a hunch_kit campaign. Copy it, or run
`hunch init-project <path>` to scaffold an empty campaign elsewhere.

```
campaign/
├── hunch_project.yaml    # Campaign defaults (provider, rubric, providers path)
├── experiments/          # One folder per experiment + experiment.yaml
├── rubrics/              # Evaluation rubrics (*.yaml)
└── providers/            # Optional local BaseProvider implementations
```

From this directory you can run:

```bash
cd examples/campaign
hunch list
hunch run _example
hunch eval _example
```

The `_example` experiment uses the built-in `echo` provider so it runs without
extra dependencies. See `providers/stub_local.py` for a minimal **local**
provider pattern (name `stub_local`); switch the manifest `provider` field to
try it.

Real work should live in a **separate** campaign directory (not inside the
hunch_kit repository), with `pip install hunch_kit` and `--root` or
`HUNCH_KIT_WORKSPACE` pointing at that directory.
