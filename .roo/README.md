# Agent CodeAnalysis

A lightweight code-analysis workspace for building and querying a compiler-derived project knowledge base.

## Overview

The agent uses three core tools inside `.roo/tools/bins/`:

1. `ghs_xref_parser.exe`  
   Parses one matching Green Hills `.elf` + `.map` pair from the same build and generates the project knowledge base JSON.

2. `ghs_xref_retriever.exe`  
   Queries the knowledge base for symbols, callers/callees, dependencies, dependents, relations, and source locations.  
   Shared human/agent session mode: `--server-studio` with prompt `xref_if>`.

3. `graph_visualizer.exe`  
   Converts the generated knowledge base into an interactive HTML graph for human inspection.

## Build Requirements

- Generate a linker MAP file.
- Keep the `.elf` and `.map` from the same build.
- Keep debug/source-line information in the ELF.
- For GHS DWARF output, enable `-dwarf2` together with a debug level such as `-g`, `-G`, or `--debug`.

## Basic Input / Output

`ghs_xref_parser.exe`  
Input: `<BUILD>.elf` + `<BUILD>.map`  
Output: `.roo/code-map/<BUILD_NAME>.json`

`ghs_xref_retriever.exe`  
Input: knowledge-base JSON + current project source root  
Typical session: `--kb <KB_PATH> --source-root <PROJECT_ROOT> --server-studio`  
Useful flags: `--max-chars`, `--list-tools`, `--describe-tools`

`graph_visualizer.exe`  
Input: knowledge-base JSON  
Output: interactive `.html` graph  
Useful options: `--output <HTML_PATH>` and `--source-root <PROJECT_ROOT>`

## Agent Flow

`/code-analysis <changeset>` → locate/confirm ELF+MAP → build KB → query KB through retriever → return evidence-based impact analysis.

The parser creates the knowledge base once per build; the retriever and visualizer reuse that result without reparsing the whole source tree.
