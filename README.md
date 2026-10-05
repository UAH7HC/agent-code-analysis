# Agent CodeAnalysis

Compiler-derived code analysis workspace for building a reusable project knowledge base (KB) and using it for dependency / impact analysis.

## What is included

- `.roo/tools/bins/ghs_xref_parser.exe` — builds the KB from one matching GHS `.elf` + `.map`.
- `.roo/tools/bins/ghs_xref_retriever.exe` — queries symbols, callers/callees, dependencies, references, and source locations.
- `.roo/tools/bins/graph_visualizer.exe` — generates an interactive HTML code graph from the KB.
- `.roo/tools/bins/get_project_folder_tree.exe` — surveys the project and helps locate candidate `.elf` / `.map` files.
- `.roo/skills/` — agent instructions for building, reading, and using the KB.

## Build prerequisites

The analyzed build should provide:
- a linker `.map` file;
- the matching `.elf` from the same build;
- debug / source-line information in the ELF;
- GHS DWARF generation enabled when DWARF-based source mapping is required (`-dwarf2` with a debug level such as `-g`, `-G`, or `--debug`).

## Basic workflow

1. Locate and confirm the correct `.elf` + `.map` pair.
2. Run `ghs_xref_parser.exe` to generate the KB.
3. Query the KB with `ghs_xref_retriever.exe`.
4. Optionally generate an interactive graph with `graph_visualizer.exe`.
5. Use `/code-analysis <changeset>` for evidence-based impact analysis.

## Input / Output

| Tool | Input | Output |
| --- | --- | --- |
| `get_project_folder_tree.exe` | Project root | Project structure / ELF-MAP discovery information |
| `ghs_xref_parser.exe` | Matching `.elf` + `.map` | `.roo/output/code-base-knowledge/<BUILD_NAME>.json` |
| `ghs_xref_retriever.exe` | KB JSON + project source root | Symbol / dependency / source-location query results |
| `graph_visualizer.exe` | KB JSON | Interactive `.html` graph |

## Retriever

Use `--server-studio` for the shared human / agent interactive session.

Useful options include:
- `--kb <KB_PATH>`
- `--source-root <PROJECT_ROOT>`
- `--server-studio`
- `--max-chars <N>`
- `--list-tools`
- `--describe-tools`

## Notes

Generated KB JSON files under `.roo/output/code-base-knowledge/` are intentionally not stored in Git because they can be large and can be regenerated from the matching build artifacts.

For detailed behavior and tool-specific guidance, see `.roo/README.md`, `.roo/tools/docs/`, and `.roo/skills/`.
