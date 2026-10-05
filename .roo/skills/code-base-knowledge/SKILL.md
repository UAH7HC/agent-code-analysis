---
name: code-base-knowledge
description: Build and maintain the project code knowledge database derived from real Green Hills output build artifacts. With the `knowledge database` (in an order word `KB`) you will know all the objects, functions, variables, dependency relationships, what is referenced by what, what references what or even casted functions call,...
---

# Code Base Knowledge

## 1. Purpose
The `knowledge database` (in an order word `KB`) is a structured map of one compiled build, not a copy of the source or a complete runtime model. Its purpose is to help identify exactly what will be affected when a function, variable, or other software entity changes.

## 2. Workflow

### 1. Identify the project root folder

Resolve the `PROJECT_ROOT` as the real project directory containing `.roo`. NOT assume current working directory is the project root folder. Use `.roo/tools/bins/get_project_folder_tree.exe` to inspect the real project structure and locate build artifacts. 

If you are in `PROJECT_ROOT`, use this command:

```bash
.\.roo\tools\bins\get_project_folder_tree.exe --root . --output .\.roo\tools\docs\project_folder_tree.txt
```
### 2. Check if knowledge database exists in `.roo/output/code-base-knowledge/` or not

**A. If knowledge database exists:** 
- Ask user to comfirm this is the knowledge database newst version, use checklist `YES` or `NO` to confirm. If user select `NO`, go to step 2B, otherwise means this is the latest version of `knowledge database`, stop all activities, break process from here as completed, then notify to ueser this task was DONE.

**B. If knowledge database does not exist or user select `NO` in step 2A:**
- Create `.roo/output/code-base-knowledge/` if it does not exist.
- Recursively find all `*.elf` and `*.map` files under `PROJECT_ROOT`.
- Group ELF/MAP files by their containing folder and collect their last-modified timestamps.
- Sort candidate folders by newest modification time first.
- If multiple candidates exist, show them as a checklist and ask the user to confirm the correct build folder.
- Do not run the parser until the user confirms the intended build.
- Retain the confirmed `ELF_PATH` and `MAP_PATH` for the next step.

***NOTE***: Use the selected build artifact filename without its extension as the output JSON filename: `.roo/output/code-base-knowledge/<BUILD_NAME>.json`.
Example: If the selected build artifact is `C:\project\build\my_build.elf`, the output JSON filename will be `.roo/output/code-base-knowledge/my_build.json`.

- Ensure the current working directory is `PROJECT_ROOT`.
- Use the confirmed `ELF_PATH` and `MAP_PATH` from Step 2.
- Set `BUILD_NAME` to the selected ELF filename without its extension.
- Run the parser and save the generated `KB` to `.roo/output/code-base-knowledge/<BUILD_NAME>.json`.

```bash
.\.roo\tools\bins\ghs_xref_parser.exe -i "<ELF_PATH>" "<MAP_PATH>" -o ".\.roo\code-base-knowledge\<BUILD_NAME>.json"
```

### 3. Verify the generated knowledge database

- Confirm `.roo/output/code-base-knowledge/<BUILD_NAME>.json` exists.
- Confirm the parser completed without errors.
- Verify the JSON file can be opened and parsed successfully.
- Check that expected knowledge structures such as functions, variables, files, and relationships are present.
- Ensure the `KB` corresponds to the confirmed `ELF_PATH` and `MAP_PATH`.
- If validation fails, report the issue and do not use the generated `KB`.
- If validation succeeds, mark the `KB` as ready for reuse by other skills.

***ATTENTION***: 
- DO NOT read entire `.roo/output/code-base-knowledge/<BUILD_NAME>.json` just verify the top-level keys to ensure all expected knowledge structures are present (functions, variables, files, relationships).

- If the current task only requires building or refreshing the `KB`, stop here after successful validation and report completion.

- If this skill is being used as sub-process of another task or invoked by another skill, continue by reading `.roo/tools/docs/GHS_XREF_PARSER_OUTPUT_MINDSET.md` to fully understand the mindset, structure, semantics, and relationships represented in the generated JSON `KB` before continuing the parent task.


## Terminology

- `KB` is the common abbreviation for `knowledge database` used across this project.
- Other skills may reference `KB`, reuse an existing KB, or invoke this `code-base-knowledge` skill when a KB needs to be created or refreshed.
- Unless another context explicitly defines otherwise, `KB` refers to the generated database stored under `.roo/output/code-base-knowledge/`.
