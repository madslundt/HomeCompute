# Architecture diagrams

Each diagram is stored as editable D2 source and as rendered SVG and PNG:

| Base name | Purpose |
| --- | --- |
| `gb10-platform` | Target two-node architecture, consumers, trust boundaries, and data placement |
| `gb10-installation` | Compute-node installation, qualification gates, and promotion path |

The SVG files are used in Markdown because they stay sharp and remain small.
The PNG files are provided for tools that do not render SVG.

After editing a `.d2` file, regenerate both outputs with D2 and commit all three
files together:

```bash
d2 diagrams/gb10-platform.d2 diagrams/gb10-platform.svg
d2 diagrams/gb10-platform.d2 diagrams/gb10-platform.png
```

Repeat with `gb10-installation`. Review the rendered result before publishing;
generated output should never be edited by hand.
