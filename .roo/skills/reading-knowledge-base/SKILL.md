# KB Reading Skill — GHS Xref Retriever Knowledge

## About this document

This skill containing consolidated knowledge from hands-on exploration of the GHS Xref Retriever tool.

**This skill covers 7 sections:**

- *1. What the KB Is* — Explains that the KB is a single JSON snapshot of a compiled build (not source code), produced from MAP/ELF/DWARF/XREF artifacts, containing a structured graph of modules, functions, variables, types, scopes, files, and their reference relationships.

- *2. Core Concepts* — Covers entity identity (UUIDs, duplicates, definition vs declaration), searchable entity kinds (function, variable, module, etc.), module = linker object semantics, reference edge meaning (A→B = A references B, NOT a proven call), and ownership vs reference distinction.

- *3. Retriever Invocation* — Executable path (.roo/tools/bins/ghs_xref_retriever.exe), KB path (.roo/code-base-knowledge/<BUILD_NAME>.json), one-shot mode syntax, interactive --server-studio mode, and key launch options (--kb, --source-root, --max-chars, --list-tools, --describe-tools).

- *4. Command Reference* — All 17 commands with syntax and usage guidance: stats, search, context, references, relation, members, connections, dependents, dependencies, function_chain, paths, source, graph, navigate/back/forward/current.

- *5. Mindset Rules* — Start small and expand selectively, handle duplicate names carefully, pagination protocol (always use next_offset), evidence interpretation (reference ≠ call ≠ execution), build vs source boundary awareness, and error handling table for all response statuses.

- *6. Common Analysis Patterns* — Ready-to-use command sequences for: "Who calls X?", "What does X depend on?", "Impact of changing V?", "Module dependencies?", "Files in module?", "Path from A to B?", "Functions in module?".

- *7. Key Limitations* — No file-path/line-to-symbol search, no confirmed-call extraction, optimization/inlining gaps, callback blind spots, module ≠ AUTOSAR component, --functions-only skips variable intermediates, graph has no pagination, source text is from checkout not build.

## 1. What the KB Is

The Knowledge Base (KB) is a **single JSON snapshot of one compiled build**, produced by the GHS Xref Parser from MAP/ELF/DWARF/XREF artifacts. It is NOT source code — it is a structured graph of modules, functions, variables, types, scopes, files, and their reference relationships as recorded by the compiler/linker.

## 2. Core Concepts

### Entity Identity

- Every entity has a **UUID** unique within the snapshot (UUIDv5). UUIDs are NOT stable across rebuilds.
- Names can be **duplicated** across modules (e.g., `CanNm_MainFunction` exists in CanNm.o as definition AND in Rte_Application_Core_0_NonTrusted.o as declaration). Always use UUID after initial search.
- `is_declaration=false` means **definition** (the real implementation). `is_declaration=true` means a forward declaration seen by another compilation unit.

### Entity Kinds

Searchable: `module`, `function`, `variable`, `parameter`, `inline_instance`, `linker_symbol`, `unresolved_symbol`.
Not searchable by name: `file`, `type`, `compilation_unit`, `scope` — query their UUIDs once found.

### Module = Linker Object

A module (e.g., `CanNm.o`) is a **linker object file**, not necessarily one C file or one AUTOSAR component. It owns symbols (functions + variables) that were compiled into it.

### Reference Edge Semantics

- `A → B` means **A references B** (A uses/depends on B).
- A reference is NOT a proven call, read/write, or execution path. It is a recorded binary cross-reference.
- `resolution: "resolved"` confirms entity association, not behavioral impact.
- `access` and `dispatch` fields are often `"unknown"` when not extracted.

### Ownership vs References

- `contains`, `parent_function_id`, `scope_chain` = **lexical structure** (who owns whom in source).
- References = **usage relationships** (who uses whom at binary level).
- A global variable with `parent_function_id: null` can still have many users found via incoming references.

## 3. Retriever Tool — Invocation

### Executable Location

```bash
.roo\tools\bins\ghs_xref_retriever.exe
```

### KB Location

```bash
.roo\code-base-knowledge\<BUILD_NAME>.json
```

### One-Shot Mode (preferred for agents)

```powershell
.roo\tools\bins\ghs_xref_retriever.exe --kb ".roo\code-base-knowledge\<BUILD_NAME>.json" <COMMAND> [ARGS]
```

### Interactive Session Mode

```powershell
.roo\tools\bins\ghs_xref_retriever.exe --kb "<KB_PATH>" --source-root "<PROJECT_ROOT>" --server-studio
```

Then enter commands at `xref_if>` prompt. Use `exit` to end.

### Key Launch Options

| Option | Purpose |
| -------- | --------- |
| `--kb PATH` | Select KB JSON file |
| `--source-root PATH` | Map recorded paths to current checkout (needed for `source` command) |
| `--max-chars N` | Response data budget (default 20,000) |
| `--list-tools` | Print 17 method names (no KB load) |
| `--describe-tools` | Print full parameter schemas (no KB load) |

## 4. Command Reference — When to Use What

### Investigation Flow

```bash
Question → search (find entity) → context (understand it) → references/members (explore relationships)
         → dependents/dependencies (expand impact) → relation/source (check evidence)
```

### `stats` — First Command Always

No arguments. Returns build ID, architecture, compiler, entity counts, coverage info.
Check `coverage.statistics` for reference counts and resolution quality.

### `search` — Find Entities by Name

```bash
search "<NAME>" --kind <function|variable|module|...> --limit N --module "<MODULE_UUID>"
```

- Case-insensitive substring match. Exact names ranked first.
- Returns: `id`, `kind`, `name`, `module_id`, `module`, `is_declaration`, `definition`, `declaration_location`.
- **Critical**: When duplicates exist, pick the one with `is_declaration: false` (the definition) for analysis.
- Use `--kind` to filter: `function`, `variable`, `module`, `parameter`, etc.
- Use `--module` to scope search to a specific module.
- Pagination: use `next_offset` from response for next page.

### `context` — Deep Dive on One Entity

```bash
context "<UUID>" --limit 5 --sites-limit 2
```

Returns: identity, owner module, definition/declaration locations, scope chain, address ranges, linker names, children (local variables), and **previews** of incoming/outgoing references with site details.

- `--limit` applies separately to children, incoming refs, outgoing refs, source files.
- `--sites-limit` limits sites per reference group.
- This is a **local overview** — use `references` and `members` for full pagination.

### `references` — Who Uses This / What Does This Use

```bash
references "<UUID>" --direction incoming --limit 10 --offset 0 --sites-limit 2
references "<UUID>" --direction outgoing --limit 10
references "<UUID>" --direction both
references "<MODULE_UUID>" --direction outgoing --peer "<OTHER_MODULE_UUID>" --limit 10
```

- `incoming` = "who references this entity?" (impact analysis)
- `outgoing` = "what does this entity reference?" (dependency analysis)
- `--peer` restricts the other endpoint to a specific entity/module.
- Each result is a **relation group** with: relation UUID, source entity, target entity, resolution, site_count, sites preview.
- `total` counts relation groups, NOT runtime calls.

### `relation` — Inspect One Relationship's Evidence

```bash
relation "<RELATION_UUID>" --limit 30
```

- Use a **relation UUID** (from `references` results), NOT an entity UUID.
- Returns: all recorded use sites with binary addresses, source locations, evidence from MAP/ELF.
- Use when `site_count` in references preview exceeds `sites_limit`.

### `members` — List What Belongs to an Entity

```bash
members "<MODULE_UUID>" --kind function --limit 20
members "<MODULE_UUID>" --kind file --limit 10
members "<FUNCTION_UUID>" --limit 10
```

- Module members: its owned functions/variables (including declarations from headers).
- `--kind file`: lists the module's source files with paths.
- Function members: its local variables and nested scopes.
- Pagination via `next_offset`.

### `connections` — Module-to-Module Links

```bash
connections --module "<MODULE_UUID>" --direction outgoing --limit 10
connections --limit 20
```

- Returns directed, grouped references **between different modules** (excludes internal refs).
- Each group: source module, target module, relation_count, site_count, sample_relation_ids.
- Use `references` with `--peer` to drill into symbol-level details.

### `dependents` — Impact Analysis (Incoming Multi-Hop)

```bash
dependents "<UUID>" --depth 2 --max-nodes 40 --max-edges 80
```

- Always follows **incoming** references. Do NOT supply `--direction`.
- Root = depth 0, direct referrers = depth 1.
- Returns `nodes` (with depth) and `edges` (with relation UUIDs and source locations).
- Edge arrows retain recorded source→target direction even during incoming traversal.
- **Warning**: "No edge does not prove absence of dependency."

### `dependencies` — Dependency Analysis (Outgoing Multi-Hop)

```bash
dependencies "<UUID>" --depth 2 --max-nodes 40 --max-edges 80
dependencies "<UUID>" --direction incoming  # can change direction
```

- Default: follows **outgoing** references. Direction can be changed.
- Same output format as `dependents`.

### `function_chain` — Function-to-Function References

```bash
function_chain "<UUID>" --direction outgoing --depth 2 --max-nodes 10 --max-edges 20
```

- Always enables `--functions-only` filter. Only follows function/inline_instance nodes.
- **Limitation**: misses callback paths through variable/table intermediates.
- References are NOT guaranteed calls.

### `paths` — Check Connection Between Two Entities

```bash
paths "<SOURCE_UUID>" --target "<TARGET_UUID>" --depth 5
```

- Finds shortest **outgoing** reference path from source to target.
- Returns `path` (node list) and `relation_ids`.
- `not_found_within_limits` does NOT prove global disconnection — just not found within depth/caps.
- Does NOT accept `--direction` or `--module`.

### `source` — Read Source Code

```bash
source "<UUID>" --radius 8
source --file-id "<FILE_UUID>" --line 100 --radius 8
```

- Requires `--source-root` mapping. Reads from **current checkout**, not build snapshot.
- `source_revision_verified=false` — content may not match the build.

### `graph` — GUI-Equivalent Graph Data

```bash
graph --view global --max-nodes 50 --max-edges 100
graph "<MODULE_UUID>" --view module --member-limit 75
graph "<SYMBOL_UUID>" --view symbol --depth 1 --direction both
```

- Views: `global` (modules), `module` (owned symbols + refs), `symbol` (reference neighborhood).
- `--no-show-functions` / `--no-show-variables` hide after traversal (intermediates still traversed).
- No pagination cursor — use `members`, `references`, `connections` for complete data.

### `navigate`/`back`/`forward`/`current` — Session History

Only useful in `--server-studio` interactive sessions. `navigate` = `graph` + records history.

## 5. Mindset Rules for Using the Retriever

### Start Small, Expand Selectively

1. Always run `stats` first to confirm build identity and coverage.
2. Use `search` with `--kind` and `--limit` to find the right entity. Never assume first result is correct.
3. Use `context` with small limits (`--limit 5 --sites-limit 2`) for initial overview.
4. Expand with `references`, `members`, `dependents` only when needed.

### Handle Duplicates Carefully

- Same name can appear in multiple modules (definition in one, declarations in others).
- Always compare: UUID, module, `is_declaration`, definition location, parent_function_id.
- Use `--module` filter when available to narrow results.

### Pagination Protocol

- Always use `next_offset` from the response — do NOT calculate manually.
- Character budget (`max_chars`) can shorten pages, making manual offset calculation wrong.
- Check `truncated` and `truncation_reason` before claiming completeness.

### Evidence Interpretation

- A reference ≠ a call. A reference ≠ a read/write. A reference ≠ runtime execution.
- `resolved` = entity association confirmed. NOT behavioral impact confirmed.
- Multiple binary addresses can map to one C source line, or have no source line at all.
- Relation counts ≠ site counts ≠ runtime execution counts.
- Empty results do NOT prove safety or independence.

### Build vs Source Boundary

- KB describes the **build snapshot**. Source excerpts come from **current checkout**.
- After code edits, source lines may shift — KB locations become stale.
- After rebuild, regenerate KB and resolve UUIDs again.
- New/renamed/deleted symbols won't match the loaded snapshot.

### Error Handling

| Response | Action |
| ---------- | -------- |
| `ambiguous` | Compare candidates, narrow by module/kind |
| `not_found` / `total=0` | Check name, kind, snapshot; try substring |
| `not_found_within_limits` | Report depth/caps; expand selectively |
| `truncated=true` | Check counts/reasons; retrieve missing evidence |
| `truncation_reason="max_chars"` | Reduce page size or increase `--max-chars` |
| `response_too_large` | Narrow request or increase budget |
| `no_source_location` | Try another definition/declaration/site |
| `source_root_required_or_unmapped` | Check `--source-root` setting |

## 6. Common Analysis Patterns

### "Who calls function X?"

```bash
search "X" --kind function → get UUID (pick is_declaration=false)
references "<UUID>" --direction incoming --limit 20 --sites-limit 2
```

### "What does function X depend on?"

```bash
context "<UUID>" --limit 10 --sites-limit 2  (check outgoing preview)
references "<UUID>" --direction outgoing --limit 20
```

### "What is the impact if I change variable V?"

```bash
search "V" --kind variable → get UUID
dependents "<UUID>" --depth 2 --max-nodes 50 --max-edges 100
```

### "Which modules does module M depend on?"

```bash
connections --module "<MODULE_UUID>" --direction outgoing --limit 20
```

### "What files belong to module M?"

```bash
members "<MODULE_UUID>" --kind file --limit 20
```

### "Is there a path from A to B?"

```bash
search "A" → UUID_A; search "B" → UUID_B
paths "<UUID_A>" --target "<UUID_B>" --depth 5
```

### "What functions are in module M?"

```bash
members "<MODULE_UUID>" --kind function --limit 50
```

## 7. Key Limitations to Remember

1. **No file-path search or line-to-symbol search** — use object/symbol names.
2. **No read/write or confirmed-call extraction** in current parser (`"not_extracted"`).
3. **Optimization/inlining** can limit coverage — absent edges don't prove independence.
4. **Callbacks, function pointers, data tables** may create indirect paths not captured.
5. **Module ≠ AUTOSAR component** — it's a linker object, possibly spanning multiple concerns.
6. **`--functions-only`** in traversal skips variable intermediates, missing callback paths.
7. **Graph output has no pagination** — use `members`/`references`/`connections` for complete data.
8. **Source text** is from current checkout, not the build — verify correspondence.
