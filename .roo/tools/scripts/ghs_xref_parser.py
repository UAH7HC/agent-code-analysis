#!/usr/bin/env python3
"""GHS MAP + ELF/DWARF cross-reference knowledge-base exporter, version 2.1.0.

Python 3.10+; dependency: pyelftools (tested with 0.33).
Install dependency if missing: python -m pip install pyelftools==0.33
That command installs the ELF/DWARF reader into the selected Python environment.

Usage: python ghs_xref_parser.py [build_folder]
The only optional positional argument is the build output directory.
Omitting it uses ../_Build_ASW/_output relative to THIS SCRIPT, not the CWD.
An explicitly supplied relative folder is relative to the current directory.
The directory must contain exactly one same-stem .elf/.map pair.
Result: knowledge_base.json beside THIS SCRIPT, replaced atomically after validation.
The input folder and current working directory do not change the output location.

Reads every compilation unit and every MAP reference, not a selected sample.
Outputs files, object modules, compilation units, functions, variables,
parameters, reachable types, linker symbols, and evidence-backed edges.
Declaration records remain separate from definitions. Scope containment does
not imply an access. MAP references do not imply read/write or direct calls.
IDs are snapshot-local; use a separate symbol-matching layer for revision diffs.

GHS DLA/DNM companions are inventoried, not decoded. No subprocess, compiler,
source modification, automatic package installation, or firmware execution.
Temporary SQLite storage limits parsing memory; DWARF caches are released per
compilation unit. Final export assembles the UUID tables in RAM before writing.
Only the final JSON is retained on success.
"""

from __future__ import annotations

import argparse
import bisect
import gc
import hashlib
import json
import ntpath
import os
import posixpath
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from collections import Counter, defaultdict, deque
from pathlib import Path

VERSION = "2.1.0"
SCHEMA_VERSION = "1.0.0"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FOLDER = SCRIPT_DIR.parent / "_Build_ASW" / "_output"
OUTPUT_NAME = "knowledge_base.json"
OUTPUT_PATH = SCRIPT_DIR / OUTPUT_NAME
OBJECT_RE = re.compile(r"(?:[^\s()]+\.o|[^\s()]+\.a\([^\s()]+\.o\))$", re.I)
HEX_RE = re.compile(r"^[0-9a-fA-F]{8,16}$")
CONTRIB_RE = re.compile(r"^\s*([0-9a-fA-F]{8,16})\+([0-9a-fA-F]{6,16})\s+(.+?)\s+(\S+(?:\.o|\.a\([^()]+\.o\)))\s*$", re.I)
GLOBAL_RE = re.compile(r"^\s*(\.\S+)?\s*([0-9a-fA-F]{8,16})\+([0-9a-fA-f]{6,16})\s+(\S+)(?:\s+([DU]))?\s*$")
SYMBOL_TAGS = {"DW_TAG_subprogram": "function", "DW_TAG_variable": "variable",
               "DW_TAG_formal_parameter": "parameter", "DW_TAG_inlined_subroutine": "inline_instance",
               "DW_TAG_lexical_block": "scope"}
TYPE_TAGS = {"DW_TAG_base_type", "DW_TAG_typedef", "DW_TAG_pointer_type", "DW_TAG_reference_type",
             "DW_TAG_rvalue_reference_type", "DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type",
             "DW_TAG_array_type", "DW_TAG_structure_type", "DW_TAG_union_type", "DW_TAG_enumeration_type",
             "DW_TAG_subroutine_type", "DW_TAG_unspecified_type", "DW_TAG_atomic_type", "DW_TAG_class_type"}


def log (message):
    print(message, flush=True)


def text (value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def digest_file (path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def path_join (directory, name):
    module = ntpath if "\\" in directory + name or re.match(r"^[A-Za-z]:", directory + name) else posixpath
    return module.normpath(module.join(directory, name)).replace("\\", "/")


def project_relative (path, compilation_directory):
    directory = compilation_directory.replace("\\", "/")
    match = re.search(r"/_Build_ASW(?:/|$)", directory, re.I)
    if not match:
        return None
    root = directory[:match.start()].rstrip("/") + "/"
    if path.casefold().startswith(root.casefold()):
        return path[len(root):]
    return None


def discover_inputs (folder):
    if not folder.is_dir():
        raise ValueError(f"Output folder does not exist: {folder}")
    files = sorted((path for path in folder.iterdir() if path.is_file()), key=lambda path: path.name.casefold())
    maps = defaultdict(list)
    for path in files:
        if path.suffix.casefold() == ".map":
            maps[path.stem.casefold()].append(path)
    pairs = [(elf, mapping) for elf in files if elf.suffix.casefold() == ".elf"
             for mapping in maps[elf.stem.casefold()]]
    if len(pairs) != 1:
        names = ", ".join(elf.name for elf, _ in pairs) or "none"
        raise ValueError(f"Expected exactly one matching ELF/MAP pair; found {len(pairs)} ({names}). "
                         "Place the intended build pair in its own output folder.")
    return pairs[0]


def resolve_input_paths (input_paths, build_folder):
    if input_paths and build_folder:
        raise ValueError(
            "Use either --inputs or build_folder, not both."
        )

    if not input_paths:
        folder = (
            Path(build_folder).expanduser().resolve()
            if build_folder
            else DEFAULT_FOLDER.resolve()
        )

        return discover_inputs(folder)

    paths = [
        Path(value).expanduser().resolve()
        for value in input_paths
    ]

    for path in paths:
        if not path.is_file():
            raise ValueError(
                f"Input file does not exist: {path}"
            )

    by_extension = {}

    for path in paths:
        extension = path.suffix.casefold()

        if extension not in (".elf", ".map"):
            raise ValueError(
                f"Unsupported input extension: {path.name}. "
                f"Expected .elf or .map."
            )

        if extension in by_extension:
            raise ValueError(
                "Expected exactly one .elf and one .map input."
            )

        by_extension[extension] = path

    if set(by_extension) != {".elf", ".map"}:
        raise ValueError(
            "Expected exactly one .elf and one .map input."
        )

    return (
        by_extension[".elf"],
        by_extension[".map"]
    )


def resolve_output_path (output):
    if output is None:
        return OUTPUT_PATH.resolve()

    path = Path(output).expanduser()

    if not path.suffix:
        path = path.with_suffix(".json")

    elif path.suffix.casefold() != ".json":
        raise ValueError(
            f"Output file must use .json extension: {path}"
        )

    path = path.resolve()

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    return path


class IntervalIndex:
    """Half-open intervals; retains overlapping candidates without nearest-match guesses."""
    def __init__ (self, rows):
        self.rows = sorted((low, high, value) for low, high, value in rows if high > low)
        self.starts = [row[0] for row in self.rows]
        self.max_ends = []
        end = -1
        for _, high, _ in self.rows:
            end = max(end, high)
            self.max_ends.append(end)

    def find (self, address):
        result = []
        index = bisect.bisect_right(self.starts, address) - 1
        while index >= 0 and self.max_ends[index] > address:
            low, high, value = self.rows[index]
            if low <= address < high:
                result.append(value)
            index -= 1
        return result


class MapData:
    def __init__ (self, path):
        self.path = path
        self.xrefs = []
        self.symbols = []
        self.contributions = []
        self.objects = set()
        self.compiler_release = None
        self.build_time = None
        section = None
        current = None
        consumer = None
        found_xref = False
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line_number, line in enumerate(stream, 1):
                stripped = line.strip()
                if line.startswith("Release:"):
                    self.compiler_release = stripped.partition(":")[2].strip()
                if line.startswith("Load Map "):
                    self.build_time = stripped[len("Load Map "):]
                    section = None
                    continue
                if stripped in ("Module Summary", "Global Cross Reference") or stripped.startswith("Global Symbols ("):
                    section = stripped
                    found_xref |= section == "Global Cross Reference"
                    continue
                if not stripped:
                    continue
                if section == "Module Summary":
                    match = CONTRIB_RE.match(line)
                    if match:
                        output_section = match[3].split("->")[-1].strip().split()[0]
                        if output_section.startswith("."):
                            self.contributions.append({"section": output_section, "start": int(match[1], 16),
                                                       "size": int(match[2], 16), "object": match[4], "line": line_number})
                            self.objects.add(match[4])
                elif section and section.startswith("Global Symbols ("):
                    match = GLOBAL_RE.match(line)
                    if match:
                        self.symbols.append({"section": match[1], "address": int(match[2], 16),
                                             "size": int(match[3], 16), "name": match[4], "line": line_number})
                elif section == "Global Cross Reference":
                    tokens = stripped.split()
                    objects = [token for token in tokens if OBJECT_RE.fullmatch(token)]
                    if not line[0].isspace():
                        current = {"raw_name": tokens[0], "owner": objects[0] if objects else None,
                                   "line": line_number, "sites": [], "consumer_rows": []}
                        self.xrefs.append(current)
                        consumer = current["owner"]
                        tokens = tokens[1:]
                    elif objects:
                        consumer = objects[0]
                        if current is not None:
                            current["consumer_rows"].append({"object": consumer, "line": line_number})
                    self.objects.update(objects)
                    if current is not None:
                        for token in tokens:
                            if HEX_RE.fullmatch(token):
                                current["sites"].append({"object": consumer, "address": int(token, 16), "line": line_number})
        if not found_xref or not self.xrefs:
            raise ValueError("MAP has no readable Global Cross Reference section. Generate the map with GHS -Mx.")
        self.debug_index = IntervalIndex([(row["start"], row["start"] + row["size"], number)
                                         for number, row in enumerate(self.contributions) if row["section"] == ".debug_info"])
        self.section_indices = {}
        groups = defaultdict(list)
        for number, row in enumerate(self.contributions):
            groups[row["section"]].append((row["start"], row["start"] + row["size"], number))
        for name, rows in groups.items():
            self.section_indices[name] = IntervalIndex(rows)


class GraphStore:
    """Disk-backed staging for nodes, edges and validation before final export."""
    def __init__ (self, path, prefix):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA synchronous=OFF")
        self.connection.execute("PRAGMA cache_size=-32768")
        self.connection.executescript("""
            CREATE TABLE nodes (id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE edges (seq INTEGER PRIMARY KEY, relation TEXT, source TEXT, target TEXT, payload TEXT);
            CREATE TABLE diagnostics (seq INTEGER PRIMARY KEY, payload TEXT);
        """)
        self.prefix = prefix
        self.ids = set()
        self.node_counts = Counter()
        self.edge_counts = Counter()
        self.diagnostic_counts = Counter()
        self.sequence = 0

    def add_node (self, node):
        if node["id"] not in self.ids:
            self.ids.add(node["id"])
            self.node_counts[node["kind"]] += 1
            self.connection.execute("INSERT INTO nodes VALUES (?, ?, ?)",
                                    (node["id"], node["kind"], json.dumps(node, ensure_ascii=False, separators=(",", ":"))))

    def patch_properties (self, node_id, updates):
        result = self.connection.execute("SELECT payload FROM nodes WHERE id=?", (node_id,)).fetchone()
        node = json.loads(result[0])
        node["properties"].update(updates)
        self.connection.execute("UPDATE nodes SET payload=? WHERE id=?",
                                (json.dumps(node, ensure_ascii=False, separators=(",", ":")), node_id))

    def edge (self, relation, source, target, evidence, **properties):
        self.sequence += 1
        edge = {"id": str(uuid.uuid5(uuid.UUID(self.prefix), f"relation:{self.sequence}")), "relation": relation,
                "source": source, "target": target, "evidence": evidence, **properties}
        self.connection.execute("INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
                                (self.sequence, relation, source, target, json.dumps(edge, ensure_ascii=False, separators=(",", ":"))))
        self.edge_counts[relation] += 1

    def diagnostic (self, code, **details):
        self.diagnostic_counts[code] += 1
        self.connection.execute("INSERT INTO diagnostics(payload) VALUES (?)",
                                (json.dumps({"code": code, **details}, ensure_ascii=False),))

    def validate (self):
        for column in ("source", "target"):
            missing = self.connection.execute(f"SELECT COUNT(*) FROM edges e LEFT JOIN nodes n ON e.{column}=n.id "
                                              f"WHERE e.{column} IS NOT NULL AND n.id IS NULL").fetchone()[0]
            if missing:
                raise ValueError(f"Internal validation failed: {missing} missing {column} nodes.")
        keys = {"module_id", "compilation_unit_id", "parent_function_id", "type_id", "target_type_id",
                "file_id", "main_file_id", "source_module_id", "source", "target", "scope_id"}
        array_keys = {"source_candidates", "target_candidates", "module_candidates", "debug_file_ids", "function_candidates", "inline_instance_ids"}

        def inspect (value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in keys and child is not None and child not in self.ids:
                        raise ValueError(f"Internal validation failed: dangling {key}: {child}")
                    if key in array_keys and any(item not in self.ids for item in child):
                        raise ValueError(f"Internal validation failed: dangling {key}")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
        for table in ("nodes", "edges"):
            for payload, in self.connection.execute(f"SELECT payload FROM {table}"):
                inspect(json.loads(payload))

    def export (self, path, metadata):
        self.connection.commit()
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write("{\n")
            for key, value in metadata.items():
                stream.write("  " + json.dumps(key) + ": " + json.dumps(value, ensure_ascii=False, indent=2) + ",\n")
            for number, table in enumerate(("nodes", "edges", "diagnostics")):
                stream.write(f'  "{table}": [\n')
                first = True
                order = "rowid" if table == "nodes" else "seq"
                for payload, in self.connection.execute(f"SELECT payload FROM {table} ORDER BY {order}"):
                    if not first:
                        stream.write(",\n")
                    stream.write("    " + payload)
                    first = False
                stream.write("\n  ]" + (",\n" if number < 2 else "\n"))
            stream.write("}\n")

    def close (self):
        self.connection.close()


class GhsParser:
    def __init__ (self, elf_path, map_path, store, ELFFile):
        self.elf_path = elf_path
        self.map_path = map_path
        self.store = store
        self.prefix = store.prefix
        self.map = MapData(map_path)
        self.stream = elf_path.open("rb")
        self.elf = ELFFile(self.stream)
        self.dwarf = self.elf.get_dwarf_info() if self.elf.has_dwarf_info(strict=True) else None
        self.contexts = {}
        self.symbols = {}
        self.by_address = defaultdict(list)
        self.by_name = defaultdict(list)
        self.elf_by_name = defaultdict(list)
        self.elf_by_address = defaultdict(list)
        self.owner_hints = defaultdict(set)
        self.type_queue = deque()
        self.type_ids = set()
        self.raw_aliases = defaultdict(set)
        self.line_lookup = defaultdict(dict)
        self.reference_addresses = defaultdict(set)
        self.producers = set()
        self.statistics = Counter()
        self.used_input_fingerprints = {}
        for row in self.map.xrefs:
            if row["owner"]:
                self.owner_hints[row["raw_name"]].add(row["owner"])
            for site in row["sites"]:
                if site["object"]:
                    self.reference_addresses[site["object"]].add(site["address"])
        for obj in sorted(self.map.objects):
            self.module(obj)
        table = self.elf.get_section_by_name(".symtab")
        if table is None:
            raise ValueError("ELF is missing .symtab; provide the unstripped build ELF.")
        self.elf_symbols = []
        for index, symbol in enumerate(table.iter_symbols()):
            section_index = symbol["st_shndx"]
            section = self.elf.get_section(section_index).name if isinstance(section_index, int) else str(section_index)
            row = {"index": index, "name": symbol.name, "address": symbol["st_value"], "size": symbol["st_size"],
                   "elf_type": symbol["st_info"]["type"], "binding": symbol["st_info"]["bind"],
                   "section": section, "defined": section_index != "SHN_UNDEF"}
            self.elf_symbols.append(row)
            self.elf_by_name[row["name"]].append(row)
            if row["defined"] and row["elf_type"] in ("STT_FUNC", "STT_OBJECT"):
                self.elf_by_address[row["address"]].append(row)
        self.check_consistency()

    def identifier (self, kind, key):
        return str(uuid.uuid5(uuid.UUID(self.prefix), f"{kind}:{key}"))

    def die_id (self, die):
        return self.identifier("die", die.offset)

    def evidence (self, die):
        return [{"artifact_id": self.identifier("artifact", "elf"), "section": ".debug_info",
                 "cu_offset": hex(die.cu.cu_offset), "die_offset": hex(die.offset)}]

    def module (self, obj):
        node_id = self.identifier("module", obj)
        self.store.add_node({"id": node_id, "kind": "module", "name": obj,
                             "properties": {"object_name": obj, "semantic_level": "object_file"},
                             "evidence": [{"artifact_id": self.identifier("artifact", "map"), "section": "Module Summary/Global Cross Reference"}]})
        return node_id

    def file (self, path, directory = ""):
        node_id = self.identifier("file", path)
        self.store.add_node({"id": node_id, "kind": "file", "name": path.rsplit("/", 1)[-1],
                             "properties": {"recorded_path": path, "project_relative_path": project_relative(path, directory),
                                            "source_contents_available": False},
                             "evidence": [{"artifact_id": self.identifier("artifact", "elf"), "section": ".debug_info/.debug_line"}]})
        return node_id

    def check_consistency (self):
        exact = {(row["name"], row["address"], row["size"]) for row in self.elf_symbols}
        seen = set()
        missing = []
        for row in self.map.symbols:
            key = (row["name"], row["address"], row["size"])
            if key not in seen:
                seen.add(key)
                if key not in exact:
                    missing.append(row)
        self.consistency = {"checked_map_symbols": len(seen), "matching_elf_symbols": len(seen) - len(missing),
                            "mismatches": len(missing)}
        if missing:
            examples = ", ".join(row["name"] for row in missing[:5])
            raise ValueError(f"MAP/ELF consistency check failed for {len(missing)} symbols ({examples}). "
                             "Use both files from the same unmodified build.")
        if not seen:
            self.store.diagnostic("map_global_symbols_not_checked", reason="Unsupported or missing Global Symbols table")

    def get_attr (self, die, key, inherit = True):
        visited = set()
        pending = [die]
        while pending and len(visited) < 32:
            current = pending.pop()
            if current.offset in visited:
                continue
            visited.add(current.offset)
            if key in current.attributes:
                return current, current.attributes[key]
            if inherit:
                for relation in ("DW_AT_specification", "DW_AT_abstract_origin"):
                    if relation in current.attributes:
                        pending.append(current.get_DIE_from_attribute(relation))
        return None, None

    def value (self, die, key, default = None, inherit = True):
        _, attribute = self.get_attr(die, key, inherit)
        return text(attribute.value) if attribute is not None else default

    def context (self, cu):
        if cu.cu_offset in self.contexts:
            return self.contexts[cu.cu_offset]
        top = cu.get_top_DIE()
        directory = self.value(top, "DW_AT_comp_dir", "")
        path = path_join(directory, self.value(top, "DW_AT_name", ""))
        matches = [self.map.contributions[index] for index in self.map.debug_index.find(cu.cu_offset)]
        matches = [row for row in matches if cu.cu_offset + cu.size <= row["start"] + row["size"]]
        objects = sorted({row["object"] for row in matches})
        ctx = {"id": self.die_id(top), "offset": cu.cu_offset, "version": cu["version"],
               "directory": directory, "path": path, "objects": objects,
               "module_id": self.module(objects[0]) if len(objects) == 1 else None,
               "files": {}, "line_program": None}
        self.contexts[cu.cu_offset] = ctx
        lp = self.dwarf.line_program_for_CU(cu)
        ctx["line_program"] = lp
        if lp:
            file_base = 0 if lp.header.version >= 5 else 1
            for index, entry in enumerate(lp["file_entry"], file_base):
                base = directory
                if lp.header.version >= 5:
                    if entry.dir_index < len(lp["include_directory"]):
                        base = path_join(base, text(lp["include_directory"][entry.dir_index]))
                elif entry.dir_index:
                    base = path_join(base, text(lp["include_directory"][entry.dir_index - 1]))
                ctx["files"][index] = self.file(path_join(base, text(entry.name)), directory)
        producer = self.value(top, "DW_AT_producer", "unknown")
        self.producers.add(producer)
        props = {"main_file_id": self.file(path, directory), "compilation_directory": directory,
                 "producer": producer, "dwarf_version": cu["version"], "module_id": ctx["module_id"],
                 "module_candidates": [self.module(obj) for obj in objects],
                 "debug_file_ids": sorted(set(ctx["files"].values()))}
        self.store.add_node({"id": ctx["id"], "kind": "compilation_unit", "name": path.rsplit("/", 1)[-1],
                             "properties": props, "evidence": self.evidence(top)})
        if ctx["module_id"]:
            self.store.edge("compiled_from", ctx["module_id"], ctx["id"],
                            [{"artifact_id": self.identifier("artifact", "map"), "line": matches[0]["line"]}, *self.evidence(top)],
                            resolution="debug_info_contribution_interval")
        else:
            self.store.diagnostic("cu_module_unresolved", cu_offset=hex(cu.cu_offset), object_candidates=objects)
        return ctx

    def location (self, die):
        owner, file_attribute = self.get_attr(die, "DW_AT_decl_file")
        if file_attribute is None:
            return None
        ctx = self.context(owner.cu)
        file_id = ctx["files"].get(file_attribute.value)
        if file_id is None:
            return None
        return {"file_id": file_id, "line": self.value(die, "DW_AT_decl_line"),
                "column": self.value(die, "DW_AT_decl_column"), "origin": "dwarf_decl_attributes",
                "attributes_die_offset": hex(owner.offset),
                "attributes_declare_only": bool(self.value(owner, "DW_AT_declaration", False, False))}

    def ranges (self, die):
        from elftools.dwarf.descriptions import describe_form_class
        result = []
        low = self.value(die, "DW_AT_low_pc", inherit=False)
        high = die.attributes.get("DW_AT_high_pc")
        if low is not None and high is not None:
            end = high.value if describe_form_class(high.form) == "address" else low + high.value
            if end > low:
                result.append((low, end))
        if "DW_AT_ranges" in die.attributes:
            base = self.value(die.cu.get_top_DIE(), "DW_AT_low_pc", 0, False)
            for entry in self.dwarf.range_lists().get_range_list_at_offset(die.attributes["DW_AT_ranges"].value, cu=die.cu):
                if hasattr(entry, "base_address"):
                    base = entry.base_address
                else:
                    start = entry.begin_offset + (0 if entry.is_absolute else base)
                    end = entry.end_offset + (0 if entry.is_absolute else base)
                    if end > start:
                        result.append((start, end))
        return sorted(set(result))

    def location_address (self, die):
        from elftools.dwarf.dwarf_expr import DWARFExprParser
        owner, attribute = self.get_attr(die, "DW_AT_location")
        if attribute is None:
            return None, None
        raw = attribute.value
        if isinstance(raw, list):
            try:
                operations = DWARFExprParser(owner.cu.structs).parse_expr(raw)
                if len(operations) == 1 and operations[0].op_name == "DW_OP_addr":
                    return operations[0].args[0], raw
            except (KeyError, ValueError, IndexError, AssertionError) as error:
                self.store.diagnostic("unsupported_location_expression", die_offset=hex(die.offset), reason=str(error))
        return None, raw

    def request_type (self, die):
        node_id = self.die_id(die)
        if node_id not in self.type_ids:
            self.type_ids.add(node_id)
            self.type_queue.append(die)
        return node_id

    def type_reference (self, die):
        owner, attribute = self.get_attr(die, "DW_AT_type")
        if attribute is None:
            return None
        return self.request_type(owner.get_DIE_from_attribute("DW_AT_type"))

    def emit_types (self):
        while self.type_queue:
            die = self.type_queue.popleft()
            type_id = self.die_id(die)
            props = {"dwarf_tag": die.tag, "byte_size": self.value(die, "DW_AT_byte_size"),
                     "cu_offset": hex(die.cu.cu_offset), "target_type_id": self.type_reference(die),
                     "members": [], "dimensions": [], "enumerators": [], "parameters": []}
            dependencies = defaultdict(list)
            if props["target_type_id"]:
                dependencies[props["target_type_id"]].append({"role": die.tag})
            for child in die.iter_children():
                if child.tag == "DW_TAG_member":
                    member = {"name": self.value(child, "DW_AT_name"), "type_id": self.type_reference(child),
                              "data_member_location_raw": self.value(child, "DW_AT_data_member_location"),
                              "bit_size": self.value(child, "DW_AT_bit_size"),
                              "bit_offset_raw": self.value(child, "DW_AT_bit_offset"),
                              "data_bit_offset": self.value(child, "DW_AT_data_bit_offset"),
                              "evidence_die_offset": hex(child.offset)}
                    props["members"].append(member)
                    if member["type_id"]:
                        dependencies[member["type_id"]].append({"role": "member", "name": member["name"]})
                elif child.tag == "DW_TAG_subrange_type":
                    props["dimensions"].append({"lower_bound": self.value(child, "DW_AT_lower_bound"),
                                                "upper_bound": self.value(child, "DW_AT_upper_bound"),
                                                "count": self.value(child, "DW_AT_count")})
                elif child.tag == "DW_TAG_enumerator":
                    props["enumerators"].append({"name": self.value(child, "DW_AT_name"), "value": self.value(child, "DW_AT_const_value")})
                elif child.tag == "DW_TAG_formal_parameter":
                    child_type = self.type_reference(child)
                    props["parameters"].append({"name": self.value(child, "DW_AT_name"), "type_id": child_type})
                    if child_type:
                        dependencies[child_type].append({"role": "parameter_type"})
            self.store.add_node({"id": type_id, "kind": "type", "name": self.value(die, "DW_AT_name"),
                                 "properties": props, "evidence": self.evidence(die)})
            for target_id, roles in dependencies.items():
                self.store.edge("uses_type", type_id, target_id, self.evidence(die), roles=roles)

    def emit_symbol (self, die, ctx):
        node_id = self.die_id(die)
        if node_id in self.symbols:
            return
        kind = SYMBOL_TAGS[die.tag]
        parents = []
        parent = die.get_parent()
        parent_function = None
        parent_declaration = False
        while parent is not None and parent.tag != "DW_TAG_compile_unit":
            if parent.tag in TYPE_TAGS:
                return
            parents.append({"tag": parent.tag, "die_offset": hex(parent.offset), "name": self.value(parent, "DW_AT_name")})
            if parent.tag in ("DW_TAG_subprogram", "DW_TAG_inlined_subroutine") and parent_function is None:
                parent_function = parent
                parent_declaration = bool(self.value(parent, "DW_AT_declaration", False, False))
            parent = parent.get_parent()
        declaration = bool(self.value(die, "DW_AT_declaration", False, False)) or (kind == "parameter" and parent_declaration)
        location = self.location(die)
        address, raw_location = self.location_address(die)
        ranges = self.ranges(die) if kind in ("function", "inline_instance", "scope") else []
        name = self.value(die, "DW_AT_name")
        size = None
        if address is not None and kind == "variable":
            sizes = {row["size"] for row in self.elf_by_address[address] if row["elf_type"] == "STT_OBJECT"}
            if len(sizes) == 1:
                size = next(iter(sizes))
        definition = location if not declaration and location and not location["attributes_declare_only"] else None
        props = {"origin": "dwarf", "module_id": ctx["module_id"], "module_candidates": [self.module(obj) for obj in ctx["objects"]],
                 "compilation_unit_id": ctx["id"], "is_declaration": declaration, "definition": definition,
                 "declaration_location": location if declaration or (location and location["attributes_declare_only"]) else None,
                 "parent_function_id": self.die_id(parent_function) if parent_function else None,
                 "scope_path": list(reversed(parents)), "external": self.value(die, "DW_AT_external"),
                 "address": hex(address) if address is not None else None, "byte_size": size,
                 "address_ranges": [{"start": hex(low), "end_exclusive": hex(high)} for low, high in ranges],
                 "location_expression_raw": raw_location, "type_id": self.type_reference(die), "raw_linker_names": [],
                 "linkage_name": self.value(die, "DW_AT_linkage_name", self.value(die, "DW_AT_MIPS_linkage_name"))}
        record = {"id": node_id, "kind": kind, "name": name, "module_id": ctx["module_id"], "cu_offset": die.cu.cu_offset,
                  "address": address, "size": size, "ranges": ranges, "declaration": declaration,
                  "external": props["external"], "linkage_name": props["linkage_name"], "parent_function_id": props["parent_function_id"]}
        self.symbols[node_id] = record
        if not declaration:
            if kind == "function":
                for low, _ in ranges:
                    self.by_address[(low, kind)].append(node_id)
            elif kind == "variable" and address is not None:
                self.by_address[(address, kind)].append(node_id)
        self.by_name[(name, kind)].append(node_id)
        self.store.add_node({"id": node_id, "kind": kind, "name": name, "properties": props, "evidence": self.evidence(die)})
        parent_scope = die.get_parent()
        while parent_scope and parent_scope.tag not in SYMBOL_TAGS and parent_scope.tag != "DW_TAG_compile_unit":
            parent_scope = parent_scope.get_parent()
        props["scope_id"] = self.die_id(parent_scope) if parent_scope else ctx["id"]
        self.store.patch_properties(node_id, {"scope_id": props["scope_id"]})
        if kind == "inline_instance":
            origin = die.get_DIE_from_attribute("DW_AT_abstract_origin") if "DW_AT_abstract_origin" in die.attributes else None
            self.store.patch_properties(node_id, {"abstract_origin_offset": hex(origin.offset) if origin else None,
                "call_location": {"file_id": ctx["files"].get(self.value(die, "DW_AT_call_file", inherit=False)),
                                  "line": self.value(die, "DW_AT_call_line", inherit=False)}})
        self.store.edge("contains", props["scope_id"], node_id, self.evidence(die),
                        containment="lexical_scope" if parent_function else "translation_unit")
        if location:
            self.store.edge("declared_in" if declaration or location["attributes_declare_only"] else "defined_in",
                            node_id, location["file_id"], self.evidence(die), location=location)
        if props["type_id"]:
            self.store.edge("returns_type" if kind in ("function", "inline_instance") else "has_type",
                            node_id, props["type_id"], self.evidence(die))

    def parse_line_locations (self, ctx):
        lp = ctx["line_program"]
        if lp is None:
            return
        queries = sorted({address for obj in ctx["objects"] for address in self.reference_addresses[obj]})
        previous = None
        for entry in lp.get_entries():
            state = entry.state
            if state is None:
                continue
            if previous is not None and previous.address < state.address:
                begin = bisect.bisect_left(queries, previous.address)
                end = bisect.bisect_left(queries, state.address)
                if end > begin and previous.line:
                    file_id = ctx["files"].get(previous.file)
                    if file_id is not None:
                        location = {"file_id": file_id, "line": previous.line, "column": previous.column or None,
                                    "instruction_interval": {"start": hex(previous.address), "end_exclusive": hex(state.address)},
                                    "origin": "dwarf_line_table"}
                        for address in queries[begin:end]:
                            candidates = self.line_lookup[ctx["offset"]].setdefault(address, [])
                            if location not in candidates:
                                candidates.append(location)
            previous = None if state.end_sequence else state

    def parse_dwarf (self):
        if self.dwarf is None:
            self.store.diagnostic("elf_has_no_dwarf", detail="Only binary symbols and MAP references can be exported.")
            return
        cus = list(self.dwarf.iter_CUs())
        self.statistics["compilation_units"] = len(cus)
        for count, cu in enumerate(cus, 1):
            ctx = self.context(cu)
            for die in cu.iter_DIEs():
                if die.tag in SYMBOL_TAGS:
                    self.emit_symbol(die, ctx)
            self.emit_types()
            self.parse_line_locations(ctx)
            ctx["line_program"] = None
            # Release pyelftools's lazy caches only after this CU's graph is materialized.
            # Offset-based references can reconstruct a released DIE if a later CU needs it.
            cu._dielist.clear()
            cu._diemap.clear()
            self.dwarf._linetable_cache.clear()
            if count % 25 == 0 or count == len(cus):
                self.store.connection.commit()
                gc.collect()
                log(f"      DWARF {count:>4}/{len(cus)} CU | {len(self.symbols):,} symbols | {len(self.type_ids):,} types")
        self.statistics["cu_with_unique_module"] = sum(bool(ctx["module_id"]) for ctx in self.contexts.values())

    def binary_candidates (self, row, owner):
        kind = {"STT_FUNC": "function", "STT_OBJECT": "variable"}.get(row["elf_type"], "linker_symbol")
        module_id = self.module(owner) if owner else None
        candidates = [sid for sid in self.by_address.get((row["address"], kind), [])
                      if self.symbols[sid]["module_id"] == module_id and module_id]
        named = [sid for sid in candidates if self.symbols[sid]["name"] == row["name"]
                 or row["name"].startswith((self.symbols[sid]["name"] or "\x00") + ".")
                 or self.symbols[sid]["linkage_name"] == row["name"]]
        if named:
            candidates = named
        if candidates:
            for sid in candidates:
                self.raw_aliases[sid].add(row["name"])
            return candidates
        sid = self.identifier("binary_symbol", (row["index"], owner))
        ranges = [(row["address"], row["address"] + row["size"])] if kind == "function" and row["size"] else []
        props = {"origin": "elf_symbol_table", "module_id": module_id, "compilation_unit_id": None,
                 "is_declaration": not row["defined"], "definition": None, "parent_function_id": None,
                 "scope_id": module_id, "scope_path": [], "type_id": None,
                 "address": hex(row["address"]) if row["defined"] else None, "byte_size": row["size"],
                 "address_ranges": [{"start": hex(a), "end_exclusive": hex(b)} for a, b in ranges],
                 "raw_linker_names": [row["name"]], "elf_type": row["elf_type"], "section": row["section"],
                 "external": row["binding"] in ("STB_GLOBAL", "STB_WEAK")}
        self.store.add_node({"id": sid, "kind": kind, "name": row["name"], "properties": props,
                             "evidence": [{"artifact_id": self.identifier("artifact", "elf"),
                                           "section": ".symtab", "symbol_index": row["index"]}]})
        if sid not in self.symbols:
            self.symbols[sid] = {"id": sid, "kind": kind, "name": row["name"], "module_id": module_id,
                "cu_offset": None, "address": row["address"] if row["defined"] else None, "size": row["size"],
                "ranges": ranges, "declaration": not row["defined"], "external": props["external"],
                "linkage_name": row["name"], "parent_function_id": None}
            if module_id:
                self.store.edge("contains", module_id, sid, [], containment="binary_ownership")
        return [sid]

    def resolve_binary_symbols (self):
        self.target_lookup = {}
        used = set()
        for xref in self.map.xrefs:
            candidates = []
            rows = [row for row in self.elf_by_name[xref["raw_name"]] if row["defined"]]
            for row in rows:
                # Same raw name may occur in multiple object files; use section contributions.
                index = self.map.section_indices.get(row["section"])
                owners = {self.map.contributions[i]["object"] for i in index.find(row["address"])} if index else set()
                if row["elf_type"] in ("STT_FUNC", "STT_OBJECT") and owners and xref["owner"] not in owners:
                    continue
                candidates.extend(self.binary_candidates(row, xref["owner"]))
                used.add(row["index"])
            if not candidates:
                sid = self.identifier("unresolved_symbol", (xref["owner"], xref["raw_name"]))
                self.store.add_node({"id": sid, "kind": "unresolved_symbol", "name": xref["raw_name"],
                    "properties": {"module_id": self.module(xref["owner"]) if xref["owner"] else None,
                                   "definition": None, "raw_linker_names": [xref["raw_name"]]},
                    "evidence": [{"artifact_id": self.identifier("artifact", "map"), "line": xref["line"]}]})
                candidates = [sid]
                self.store.diagnostic("map_target_without_binary_match", name=xref["raw_name"], map_line=xref["line"])
            self.target_lookup[(xref["owner"], xref["raw_name"])] = sorted(set(candidates))
        for row in self.elf_symbols:
            if row["index"] in used or not row["defined"] or row["elf_type"] not in ("STT_FUNC", "STT_OBJECT"):
                continue
            index = self.map.section_indices.get(row["section"])
            owners = {self.map.contributions[i]["object"] for i in index.find(row["address"])} if index else set()
            self.binary_candidates(row, next(iter(owners)) if len(owners) == 1 else None)
        for sid, names in self.raw_aliases.items():
            self.store.patch_properties(sid, {"raw_linker_names": sorted(names)})

    def resolve_references (self):
        grouped = defaultdict(list)
        inlines = defaultdict(list)
        for sid, record in self.symbols.items():
            if record["declaration"] or not record["module_id"]:
                continue
            if record["kind"] in ("function", "inline_instance"):
                dest = inlines if record["kind"] == "inline_instance" else grouped
                for a, b in record["ranges"]:
                    dest[record["module_id"]].append((a, b, sid))
            elif record["kind"] == "variable" and record["address"] is not None and record["size"]:
                grouped[record["module_id"]].append((record["address"], record["address"] + record["size"], sid))
        indices = {mid: IntervalIndex(rows) for mid, rows in grouped.items()}
        inline_indices = {mid: IntervalIndex(rows) for mid, rows in inlines.items()}
        for xref in self.map.xrefs:
            targets = self.target_lookup[(xref["owner"], xref["raw_name"])]
            sites = list(xref["sites"])
            addressed_objects = {site["object"] for site in sites}
            for consumer in xref["consumer_rows"]:
                if consumer["object"] not in addressed_objects:
                    sites.append({**consumer, "address": None})
            for site in sites:
                mid = self.module(site["object"]) if site["object"] else None
                address = site["address"]
                sources = sorted(set(indices[mid].find(address))) if mid in indices and address is not None else []
                inline_ids = sorted(set(inline_indices[mid].find(address))) if mid in inline_indices and address is not None else []
                locations = []
                if len(sources) == 1 and self.symbols[sources[0]]["kind"] == "function":
                    cu_offset = self.symbols[sources[0]]["cu_offset"]
                    if cu_offset is not None:
                        locations = self.line_lookup[cu_offset].get(address, [])
                status = "resolved" if len(sources) == len(targets) == 1 else "ambiguous" if len(sources) > 1 or len(targets) > 1 else "module_only"
                if any(target not in self.symbols for target in targets):
                    status = "target_unresolved"
                self.statistics["references_" + status] += 1
                self.statistics["references_with_source_line"] += len(locations) == 1
                self.store.edge("references", sources[0] if len(sources) == 1 else mid, targets[0] if len(targets) == 1 else None,
                    [{"artifact_id": self.identifier("artifact", "map"), "line": site["line"], "symbol_line": xref["line"]},
                     {"artifact_id": self.identifier("artifact", "elf"), "section": ".symtab/.debug_info/.debug_line"}],
                    source_module_id=mid, target_raw_name=xref["raw_name"], source_candidates=sources,
                    target_candidates=targets, resolution=status, access="unknown", dispatch="unknown",
                    reference_address=hex(address) if address is not None else None,
                    source_location=locations[0] if len(locations) == 1 else None,
                    source_location_candidates=locations, inline_instance_ids=inline_ids,
                    method="map_reference_address_joined_to_symbol_ranges")


def export_knowledge_base (store, path, metadata):
    """One canonical UUID table per entity category; reference sites grouped without losing evidence."""
    tables = {key: {} for key in ("files", "modules", "compilation_units", "scopes", "symbols", "types", "relations")}
    kinds = {"file": "files", "module": "modules", "compilation_unit": "compilation_units", "scope": "scopes", "type": "types"}
    for payload, in store.connection.execute("SELECT payload FROM nodes ORDER BY rowid"):
        node = json.loads(payload)
        record = {"kind": node["kind"], "name": node["name"], **node["properties"], "evidence": node["evidence"]}
        tables[kinds.get(node["kind"], "symbols")][node["id"]] = record
    for module in tables["modules"].values():
        module.update(function_ids=[], variable_ids=[], compilation_unit_ids=[], source_file_ids=[])
    for cid, cu in tables["compilation_units"].items():
        mid = cu.get("module_id")
        if mid:
            module = tables["modules"][mid]
            module["compilation_unit_ids"].append(cid)
            if cu["main_file_id"] not in module["source_file_ids"]:
                module["source_file_ids"].append(cu["main_file_id"])
    for sid, symbol in tables["symbols"].items():
        mid = symbol.get("module_id")
        if mid and not symbol.get("is_declaration"):
            key = {"function": "function_ids", "variable": "variable_ids"}.get(symbol["kind"])
            if key:
                tables["modules"][mid][key].append(sid)
    group_ids = {}
    for payload, in store.connection.execute("SELECT payload FROM edges ORDER BY seq"):
        edge = json.loads(payload)
        eid = edge.pop("id")
        if edge["relation"] != "references":
            tables["relations"][eid] = edge
            continue
        group = (edge["source"], edge["target"], edge["source_module_id"], edge["target_raw_name"],
                 tuple(edge["source_candidates"]), tuple(edge["target_candidates"]))
        if group not in group_ids:
            group_ids[group] = eid
            tables["relations"][eid] = {key: edge[key] for key in ("relation", "source", "target", "source_module_id",
                "target_raw_name", "source_candidates", "target_candidates", "resolution", "method")}
            tables["relations"][eid]["sites"] = []
        tables["relations"][group_ids[group]]["sites"].append({key: edge[key] for key in ("reference_address",
            "source_location", "source_location_candidates", "inline_instance_ids", "access", "dispatch", "evidence")})
    diagnostics = {}
    for seq, payload in store.connection.execute("SELECT seq,payload FROM diagnostics ORDER BY seq"):
        diagnostics[str(uuid.uuid5(uuid.UUID(store.prefix), f"diagnostic:{seq}"))] = json.loads(payload)
    metadata["coverage"]["entity_counts"] = {key: len(value) for key, value in tables.items()}
    all_ids = [key for table in tables.values() for key in table] + list(diagnostics) + list(metadata["artifacts"]) + [metadata["build"]["id"]]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Duplicate UUID across tables")
    for value in all_ids:
        uuid.UUID(value)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        # Entity per line keeps even large builds inspectable without huge whitespace overhead.
        stream.write("{\n")
        first = True
        for key, value in {**metadata, **tables, "diagnostics": diagnostics}.items():
            if not first:
                stream.write(",\n")
            first = False
            stream.write(json.dumps(key) + ": ")
            if key in tables or key == "diagnostics":
                stream.write("{\n")
                for i, (rid, item) in enumerate(value.items()):
                    stream.write((",\n" if i else "") + json.dumps(rid) + ": " + json.dumps(item, ensure_ascii=False))
                stream.write("\n}")
            else:
                json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n}\n")


def main ():
    cli = argparse.ArgumentParser(
        description=__doc__
    )

    cli.add_argument(
        "build_folder",
        nargs="?",
        help=(
            "Legacy input directory containing one matching "
            ".elf/.map pair"
        )
    )

    cli.add_argument(
        "-i",
        "--inputs",
        nargs=2,
        metavar=("PATH_1", "PATH_2"),
        help=(
            "Explicit input files: exactly one .elf and one .map; "
            "order does not matter"
        )
    )

    cli.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_JSON",
        help=(
            "Output JSON file path/name; default is "
            "knowledge_base.json beside this script"
        )
    )

    args = cli.parse_args()

    try:
        from elftools.elf.elffile import ELFFile

    except ImportError:
        cli.exit(
            2,
            "Missing pyelftools. "
            "Install the packages in requirements.txt first.\n"
        )

    started = time.monotonic()

    try:
        elf_path, map_path = resolve_input_paths(
            args.inputs,
            args.build_folder
        )

        output_path = resolve_output_path(
            args.output
        )

        hashes = {
            "elf": digest_file(elf_path),
            "map": digest_file(map_path)
        }

        snapshot = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "ghs-xref:"
                + hashes["elf"]
                + hashes["map"]
            )
        )

        with tempfile.TemporaryDirectory(
            prefix="ghs_xref_"
        ) as temporary:

            store = GraphStore(
                Path(temporary) / "graph.sqlite",
                snapshot
            )

            parser = None

            try:
                log(
                    "[1/4] MAP / ELF consistency "
                    "and object ownership"
                )

                parser = GhsParser(
                    elf_path,
                    map_path,
                    store,
                    ELFFile
                )

                log(
                    "[2/4] Extract every DWARF compilation "
                    "unit, scope and type"
                )

                parser.parse_dwarf()

                log(
                    "[3/4] Resolve binary symbols "
                    "and every MAP reference"
                )

                parser.resolve_binary_symbols()
                parser.resolve_references()

                store.validate()

                metadata = {
                    "schema_version": SCHEMA_VERSION,

                    "build": {
                        "id": snapshot,
                        "parser_version": VERSION,
                        "architecture": parser.elf["e_machine"],
                        "compiler_producers": sorted(parser.producers),
                        "revision": None,
                        "variant": None,
                        "input_consistency": parser.consistency,
                        "id_policy": (
                            "UUIDv5, unique in this snapshot; "
                            "not cross-revision identity"
                        )
                    },

                    "artifacts": {
                        parser.identifier(
                            "artifact",
                            kind
                        ): {
                            "name": path.name,
                            "kind": kind,
                            "sha256": hashes[kind]
                        }

                        for kind, path in (
                            ("elf", elf_path),
                            ("map", map_path)
                        )
                    },

                    "coverage": {
                        "mode": "whole_build",
                        "statistics": dict(parser.statistics),

                        "map_symbols": len(
                            parser.map.xrefs
                        ),

                        "map_reference_addresses": sum(
                            len(row["sites"])
                            for row in parser.map.xrefs
                        ),

                        "read_write": "not_extracted",
                        "confirmed_calls": "not_extracted",
                        "source_text": "not_supplied",
                        "ghs_proprietary_debug": "not_decoded",
                        "expected_source_files": None,

                        "meaning": (
                            "All available build records processed; "
                            "source usages removed by optimization or "
                            "outside this variant are not guaranteed."
                        ),

                        "diagnostic_counts": dict(
                            store.diagnostic_counts
                        )
                    }
                }

                log(
                    f"[4/4] Write {output_path.name} "
                    f"with UUID tables"
                )

                temporary_output = output_path.with_name(
                    output_path.name + ".tmp"
                )

                try:
                    export_knowledge_base(
                        store,
                        temporary_output,
                        metadata
                    )

                    temporary_output.replace(
                        output_path
                    )

                finally:
                    temporary_output.unlink(
                        missing_ok=True
                    )

                log(
                    json.dumps(
                        metadata["coverage"]["entity_counts"]
                    )
                )

                log(
                    f"[OK] {output_path} "
                    f"({time.monotonic() - started:.1f}s)"
                )

            finally:
                if parser is not None:
                    parser.stream.close()

                store.close()

    except (
        OSError,
        ValueError,
        KeyError,
        AssertionError
    ) as error:

        cli.exit(
            1,
            f"[ERROR] {error}\n"
        )


if __name__ == "__main__":
    main()
