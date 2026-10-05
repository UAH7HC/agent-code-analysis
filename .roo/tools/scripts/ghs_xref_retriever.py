#!/usr/bin/env python3
"""Explore knowledge_base.json with a shared command session or one-shot queries.

With no method, start the persistent xref_if> prompt for humans and agents.
--server-studio explicitly starts the same session. Enter one command per line.
An explicit method runs once. Session output includes a banner and prompts;
query results are formatted JSON. This is a command console, not JSONL or MCP.
Standard library only, Python 3.10+. Import KnowledgeBase for a retained reader.
Responses never classify a MAP reference as a proven call.
By default, read knowledge_base.json beside this script, regardless of the CWD.
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import re
import shlex
import sys
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import quote

DEFAULT_KB = Path(__file__).resolve().parent / "knowledge_base.json"
RETRIEVER_VERSION = "2.3.0"
ENTITY_TABLES = ("files", "modules", "compilation_units", "scopes", "symbols", "types")
TOOLS = {
    "search": "Find symbols/modules by UUID, exact name or substring; parameters query, kind, module, limit, offset.",
    "context": "Definition, type, lexical scope, children and incoming/outgoing references; query, module, limit, sites_limit.",
    "references": "Page incoming/outgoing references; query, module, direction, limit, offset, sites_limit.",
    "relation": "Page the source locations of one relation UUID; query, limit, offset.",
    "dependencies": "Outgoing dependencies with depth, max_nodes, max_edges; query, module, direction, functions_only, confirmed_calls_only.",
    "dependents": "Incoming references and potential affected entities; same parameters as dependencies.",
    "function_chain": "Function-to-function references, NOT guaranteed calls; same parameters as dependencies.",
    "paths": "Shortest reference path between two symbols or two modules; query, target, depth.",
    "source": "Read source around a recorded location; query or file_id and line, radius. Requires source_root.",
    "stats": "Build identity, coverage and entity counts.",
    "graph": "GUI-equivalent global, module or symbol graph. Visibility filters apply after traversal; references are not proven calls.",
    "members": "Page a module's owned symbols, a function's locals or a scope's children; query, kind, limit, offset.",
    "connections": "Page aggregated directed module connections, optionally incident to one module; module, direction, limit, offset.",
    "navigate": "Open a graph and record history in the current server-studio or Python session; same parameters as graph.",
    "back": "Reopen the previous graph in this retained session.",
    "forward": "Reopen the next graph in this retained session.",
    "current": "Return the current graph and navigation state without moving."
}


def editor_uri (path, line, column = 1):
    if not path or not line or not re.match(r"^(?:[A-Za-z]:[/\\]|/)", path):
        return None
    return "vscode://file/" + quote(path.replace("\\", "/").lstrip("/"), safe="/:") + f":{line}:{column or 1}"


class KnowledgeBase:
    def __init__ (self, path, source_root = None):
        self.path = Path(path).expanduser().resolve()
        with self.path.open(encoding="utf-8") as stream:
            self.data = json.load(stream)
        if not isinstance(self.data, dict) or self.data.get("schema_version") != "1.0.0" or not all(
                isinstance(self.data.get(k), dict) for k in ENTITY_TABLES + ("relations",)):
            raise ValueError("Unsupported knowledge base schema; generate it with the supplied ghs_xref_parser.py")
        self.entities = {key: value for table in ENTITY_TABLES for key, value in self.data[table].items()}
        self.source_root = Path(source_root).expanduser().resolve() if source_root else None
        self.names = defaultdict(list)
        self.incoming = defaultdict(list)
        self.outgoing = defaultdict(list)
        self.module_incoming = defaultdict(list)
        self.module_outgoing = defaultdict(list)
        self.children = defaultdict(list)
        self.module_symbols = defaultdict(list)
        self.function_locals = defaultdict(list)
        self.inline_outgoing = defaultdict(list)
        self.reference_ids = []
        self._graph_index = None
        self._history = []
        self._history_index = -1
        for sid, value in self.entities.items():
            if value.get("name") and value["kind"] not in ("type", "file", "compilation_unit", "scope"):
                self.names[value["name"].casefold()].append(sid)
                if value["kind"] == "module" and value["name"].endswith(".o"):
                    self.names[value["name"][:-2].casefold()].append(sid)
            if value.get("scope_id"):
                self.children[value["scope_id"]].append(sid)
            if value.get("module_id") and sid in self.data["symbols"]:
                self.module_symbols[value["module_id"]].append(sid)
            if value.get("parent_function_id") and value["kind"] in ("variable", "parameter", "inline_instance", "function"):
                self.function_locals[value["parent_function_id"]].append(sid)
        for rid, rel in self.data["relations"].items():
            if rel.get("source"):
                self.outgoing[rel["source"]].append(rid)
            if rel.get("target"):
                self.incoming[rel["target"]].append(rid)
            if rel["relation"] != "references":
                continue
            self.reference_ids.append(rid)
            for inline in {iid for site in rel.get("sites", []) for iid in site.get("inline_instance_ids", [])}:
                self.inline_outgoing[inline].append(rid)
            sm = self.module_id(rel.get("source")) or rel.get("source_module_id")
            tm = self.module_id(rel.get("target"))
            if sm:
                self.module_outgoing[sm].append(rid)
            if tm:
                self.module_incoming[tm].append(rid)

    def module_id (self, sid):
        value = self.entities.get(sid, {})
        return sid if value.get("kind") == "module" else value.get("module_id")

    def location (self, location):
        if not location:
            return None
        file = self.data["files"].get(location.get("file_id"), {})
        result = {**location, "recorded_path": file.get("recorded_path"), "project_relative_path": file.get("project_relative_path")}
        mapped = self.local_path(file)
        result["local_path"] = str(mapped) if mapped else None
        result["editor_uri"] = editor_uri(str(mapped) if mapped else file.get("recorded_path"), location.get("line"), location.get("column"))
        return result

    def local_path (self, file):
        if self.source_root is None or not file.get("project_relative_path"):
            return None
        candidate = (self.source_root / file["project_relative_path"]).resolve()
        return candidate if candidate.is_relative_to(self.source_root) else None

    def brief (self, sid):
        value = self.entities[sid]
        mid = self.module_id(sid)
        result = {"id": sid, "kind": value["kind"], "name": value.get("name"), "module_id": mid,
                "module": self.data["modules"].get(mid, {}).get("name"),
                "is_declaration": value.get("is_declaration", False),
                "definition": self.location(value.get("definition")),
                "declaration_location": self.location(value.get("declaration_location")),
                "parent_function_id": value.get("parent_function_id")}
        if value["kind"] == "file":
            result["location"] = self.location({"file_id": sid, "line": 1})
        return result

    def resolve (self, query, module = None, kind = None):
        if query in self.entities:
            ids = [query]
        else:
            ids = list(self.names.get(query.casefold(), []))
            exact = [sid for sid in ids if self.entities[sid].get("name") == query]
            if exact:
                ids = exact
        if module:
            ids = [sid for sid in ids if self.module_id(sid) == module or
                   self.data["modules"].get(self.module_id(sid), {}).get("name", "").casefold() in
                   (module.casefold(), module.casefold() + ".o")]
        if kind:
            ids = [sid for sid in ids if self.entities[sid]["kind"] == kind]
        definitions = [sid for sid in ids if not self.entities[sid].get("is_declaration")]
        if definitions:
            ids = definitions
        if len(ids) != 1:
            return None, {"status": "ambiguous" if ids else "not_found", "query": query, "candidate_count": len(ids),
                          "candidates": [self.brief(sid) for sid in ids[:30]],
                          "hint": "Use a candidate UUID, or constrain module/kind; names are not unique."}
        return ids[0], None

    def search (self, query, kind = None, module = None, limit = 20, offset = 0):
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        q = query.casefold()
        ids = [query] if query in self.entities else list(dict.fromkeys(sid for name, values in self.names.items() if q in name for sid in values))
        if kind:
            ids = [sid for sid in ids if self.entities[sid]["kind"] == kind]
        if module:
            ids = [sid for sid in ids if self.module_id(sid) == module or
                   module.casefold() in self.data["modules"].get(self.module_id(sid), {}).get("name", "").casefold()]
        ids.sort(key=lambda sid: (self.entities[sid].get("name", "").casefold() != q,
                                 bool(self.entities[sid].get("is_declaration")), self.entities[sid].get("name", ""), sid))
        page = ids[offset:offset + limit]
        return {"status": "ok", "total": len(ids), "offset": offset, "next_offset": offset + len(page) if offset + len(page) < len(ids) else None,
                "results": [self.brief(sid) for sid in page]}

    def type_summary (self, tid, depth = 5, visited = None):
        if not tid:
            return None
        visited = set() if visited is None else set(visited)
        value = self.data["types"].get(tid)
        if not value:
            return {"id": tid, "status": "unresolved"}
        result = {"id": tid, "name": value.get("name"), "tag": value.get("dwarf_tag"), "byte_size": value.get("byte_size")}
        if depth <= 0 or tid in visited:
            result["expansion_stopped"] = True
            return result
        visited.add(tid)
        if value.get("dimensions"):
            result["dimensions"] = value["dimensions"]
        if value.get("target_type_id"):
            result["target"] = self.type_summary(value["target_type_id"], depth - 1, visited)
        if value.get("members"):
            result["member_count"] = len(value["members"])
            result["members_preview"] = [{"name": m["name"], "type_id": m["type_id"], "bit_size": m.get("bit_size")} for m in value["members"][:8]]
        return result

    def relation_summary (self, rid, sites_limit = 3):
        rel = self.data["relations"][rid]
        result = {"id": rid, "relation": rel["relation"], "source": self.brief(rel["source"]) if rel.get("source") else None,
                  "target": self.brief(rel["target"]) if rel.get("target") else None, "resolution": rel.get("resolution"),
                  "target_raw_name": rel.get("target_raw_name")}
        if rel["relation"] == "references":
            result.update(site_count=len(rel["sites"]), source_candidates=rel.get("source_candidates", []),
                          target_candidates=rel.get("target_candidates", []),
                          sites=[{**site, "source_location": self.location(site.get("source_location"))}
                                 for site in rel["sites"][:max(0, min(int(sites_limit), 30))]])
        return result

    def scope_chain (self, sid):
        chain, seen = [], {sid}
        parent = self.entities[sid].get("scope_id") or self.entities[sid].get("parent_function_id")
        while parent and parent in self.entities and parent not in seen:
            seen.add(parent)
            chain.append(self.brief(parent))
            parent = self.entities[parent].get("scope_id") or self.entities[parent].get("parent_function_id")
        return chain

    def context (self, query, module = None, limit = 20, sites_limit = 3):
        sid, error = self.resolve(query, module)
        if error:
            return error
        value = self.entities[sid]
        count = max(1, min(int(limit), 200))
        is_module = value["kind"] == "module"
        incoming = self.module_incoming[sid] if is_module else self.incoming[sid]
        outgoing = self.module_outgoing[sid] if is_module else self.outgoing[sid]
        if value["kind"] == "inline_instance":
            outgoing = list(dict.fromkeys(outgoing + self.inline_outgoing[sid]))
        incoming = [rid for rid in incoming if self.data["relations"][rid]["relation"] == "references"]
        outgoing = [rid for rid in outgoing if self.data["relations"][rid]["relation"] == "references"]
        children = self.module_symbols[sid] if is_module else self.function_locals[sid] if value["kind"] in ("function", "inline_instance") else self.children[sid]
        files = value.get("source_file_ids", []) if is_module else []
        return {"status": "ok", "entity": self.brief(sid), "type": self.type_summary(value.get("type_id")),
                "source_file_count": len(files),
                "source_files": [self.location({"file_id": fid, "line": 1}) for fid in files[:count]],
                "scope_chain": self.scope_chain(sid), "scope_path": value.get("scope_path", []),
                "address": value.get("address"), "address_ranges": value.get("address_ranges", []),
                "raw_linker_names": value.get("raw_linker_names", []),
                "child_count": len(children), "children": [self.brief(c) for c in children[:count]],
                "incoming_count": len(incoming), "outgoing_count": len(outgoing),
                "incoming": [self.relation_summary(r, sites_limit) for r in incoming[:count]],
                "outgoing": [self.relation_summary(r, sites_limit) for r in outgoing[:count]],
                "truncated": any(len(x) > count for x in (children, incoming, outgoing, files)),
                "interpretation": "Scope is lexical containment. References are not proven runtime calls or read/write operations."}

    def references (self, query, module = None, direction = "incoming", limit = 20, offset = 0, sites_limit = 3, peer = None):
        sid, error = self.resolve(query, module)
        if error:
            return error
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction must be incoming, outgoing or both")
        is_module = self.entities[sid]["kind"] == "module"
        inc = self.module_incoming[sid] if is_module else self.incoming[sid]
        out = self.module_outgoing[sid] if is_module else self.outgoing[sid] + self.inline_outgoing[sid]
        ids = list(dict.fromkeys((inc if direction != "outgoing" else []) + (out if direction != "incoming" else [])))
        ids = [rid for rid in ids if self.data["relations"][rid]["relation"] == "references"]
        if peer:
            peer_id, error = self.resolve(peer)
            if error:
                return error
            peer_is_module = self.entities[peer_id]["kind"] == "module"
            incoming_ids, outgoing_ids = set(inc), set(out)
            def matches (rid):
                rel = self.data["relations"][rid]
                others = []
                if direction != "outgoing" and rid in incoming_ids:
                    others.append(rel.get("source"))
                if direction != "incoming" and rid in outgoing_ids:
                    others.append(rel.get("target"))
                return any((self.module_id(other) if peer_is_module else other) == peer_id for other in others)
            ids = [rid for rid in ids if matches(rid)]
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        page = ids[offset:offset + limit]
        return {"status": "ok", "total": len(ids), "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < len(ids) else None,
                "results": [self.relation_summary(rid, sites_limit) for rid in page]}

    def members (self, query, kind = None, limit = 20, offset = 0):
        sid, error = self.resolve(query)
        if error:
            return error
        value = self.entities[sid]
        ids = value.get("source_file_ids", []) if value["kind"] == "module" and kind == "file" else self.module_symbols[sid] if value["kind"] == "module" else self.function_locals[sid] if value["kind"] in ("function", "inline_instance") else self.children[sid]
        if kind:
            ids = [other for other in ids if self.entities[other]["kind"] == kind]
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        page = ids[offset:offset + limit]
        return {"status": "ok", "owner": self.brief(sid), "total": len(ids), "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < len(ids) else None,
                "results": [self.brief(other) for other in page]}

    def _gui_index (self):
        if self._graph_index is not None:
            return self._graph_index
        endpoints = {sid for rid in self.reference_ids for sid in
                     (self.data["relations"][rid].get("source"), self.data["relations"][rid].get("target")) if sid}
        symbols = [sid for sid, value in self.data["symbols"].items() if not value.get("is_declaration") or sid in endpoints]
        entity_ids = set(self.data["modules"]) | set(symbols)
        owned, incoming, outgoing, incident, groups = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list), {}
        for sid in symbols:
            if self.entities[sid]["kind"] in ("function", "variable"):
                owned[self.module_id(sid)].append(sid)
        for rid in self.reference_ids:
            rel = self.data["relations"][rid]
            source, target = rel.get("source"), rel.get("target")
            outgoing[source].append(rid)
            incoming[target].append(rid)
            sm = self.module_id(source) or rel.get("source_module_id")
            tm = self.module_id(target)
            if sm:
                incident[sm].append(rid)
            if tm and tm != sm:
                incident[tm].append(rid)
            if sm in self.data["modules"] and tm in self.data["modules"] and sm != tm:
                group = groups.setdefault((sm, tm), {"source": sm, "target": tm, "relation_count": 0, "site_count": 0, "sample_relation_ids": []})
                group["relation_count"] += 1
                group["site_count"] += len(rel.get("sites", []))
                if len(group["sample_relation_ids"]) < 3:
                    group["sample_relation_ids"].append(rid)
        self._graph_index = {"entities": entity_ids, "owned": owned, "incoming": incoming, "outgoing": outgoing,
                             "incident": incident, "connections": list(groups.values())}
        return self._graph_index

    def connections (self, module = None, direction = "both", limit = 20, offset = 0):
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction must be incoming, outgoing or both")
        groups = self._gui_index()["connections"]
        if module:
            mid, error = self.resolve(module, kind="module")
            if error:
                return error
            groups = [g for g in groups if (direction != "incoming" and g["source"] == mid) or
                      (direction != "outgoing" and g["target"] == mid)]
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        page = groups[offset:offset + limit]
        return {"status": "ok", "total": len(groups), "offset": offset,
                "next_offset": offset + len(page) if offset + len(page) < len(groups) else None,
                "results": [{**g, "source_entity": self.brief(g["source"]), "target_entity": self.brief(g["target"])} for g in page],
                "interpretation": "Directed inter-module reference groups; internal references are excluded, as in GUI Global view."}

    def _graph_edge (self, rid):
        rel = self.data["relations"][rid]
        target_kind = self.entities.get(rel.get("target"), {}).get("kind")
        style = "dash-dot" if rel.get("resolution") not in (None, "resolved") else "dashed" if target_kind in ("variable", "parameter", "linker_symbol", "unresolved_symbol") else "solid"
        return {"id": rid, "source": rel.get("source"), "target": rel.get("target"), "relation": "references",
                "resolution": rel.get("resolution"), "line_style": style, "site_count": len(rel.get("sites", [])),
                "source_location": self.location(next((s["source_location"] for s in rel.get("sites", []) if s.get("source_location")), None))}

    def graph (self, query = None, view = "auto", depth = 1, direction = "both", max_nodes = 150, max_edges = 1000, member_limit = 75, show_functions = True, show_variables = True):
        if view not in ("auto", "global", "module", "symbol"):
            raise ValueError("view must be auto, global, module or symbol")
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction must be incoming, outgoing or both")
        if not isinstance(show_functions, bool) or not isinstance(show_variables, bool):
            raise ValueError("show_functions and show_variables must be JSON booleans")
        index = self._gui_index()
        root = None
        if query is not None:
            root, error = self.resolve(query)
            if error:
                return error
        if view == "auto":
            view = "global" if root is None else "module" if root in self.data["modules"] else "symbol"
        if view == "global" and root is not None:
            raise ValueError("Global view has no query; omit query or choose another view")
        if view != "global" and root is None:
            raise ValueError("Module and symbol views require query")
        if view == "module" and root not in self.data["modules"]:
            root = self.module_id(root)
            if root not in self.data["modules"]:
                return {"status": "no_owner_module", "query": query}
        if view == "symbol" and (root in self.data["modules"] or root not in index["entities"]):
            return {"status": "unsupported_graph_entity", "query": query,
                    "hint": "Use context for types, files, scopes or non-visualized declarations; use module view for modules."}
        depth = max(0, min(int(depth), 20))
        max_nodes, max_edges = max(1, min(int(max_nodes), 2000)), max(1, min(int(max_edges), 5000))
        member_limit = max(1, min(int(member_limit), 2000))
        levels, edges, seen_edges, reasons = {}, [], set(), set()
        def add_edge (edge):
            if len(edges) >= max_edges:
                reasons.add("max_edges")
                return False
            edges.append(edge)
            return True
        if view == "global":
            modules = sorted(self.data["modules"], key=lambda sid: (self.entities[sid].get("name", "").casefold(), sid))
            levels = {sid: None for sid in modules[:max_nodes]}
            if len(modules) > max_nodes:
                reasons.add("max_nodes")
            for group in index["connections"]:
                if group["source"] in levels and group["target"] in levels:
                    add_edge({**group, "relation": "module_reference", "line_style": "solid", "aggregated": True})
            totals = {"modules": len(modules), "connections": len(index["connections"])}
        elif view == "module":
            owned = index["owned"][root]
            selected = owned[:min(member_limit, max_nodes - 1)]
            levels = {root: 0, **{sid: 1 for sid in selected}}
            if len(owned) > member_limit:
                reasons.add("member_limit")
            if len(owned) > max_nodes - 1:
                reasons.add("max_nodes")
            for rid in index["incident"][root]:
                rel = self.data["relations"][rid]
                source, target = rel.get("source"), rel.get("target")
                if source not in levels and target not in levels:
                    continue
                if source not in index["entities"] or target not in index["entities"]:
                    reasons.add("unresolved_endpoints")
                    continue
                extra = {source, target} - levels.keys()
                if len(levels) + len(extra) > max_nodes:
                    reasons.add("max_nodes")
                    continue
                if add_edge(self._graph_edge(rid)):
                    levels.update({sid: 1 for sid in extra})
            for sid in owned:
                if sid in levels:
                    add_edge({"source": root, "target": sid, "relation": "contains", "ownership": True,
                              "line_style": "dotted", "derived_from": "module_id"})
            totals = {"owned_symbols": len(owned), "incident_relations": len(index["incident"][root])}
        else:
            levels, queue = {root: 0}, deque([root])
            while queue:
                current = queue.popleft()
                level = levels[current]
                if level >= depth:
                    continue
                adjacent = (index["outgoing"][current] if direction != "incoming" else []) + (index["incoming"][current] if direction != "outgoing" else [])
                for rid in adjacent:
                    if rid in seen_edges:
                        continue
                    rel = self.data["relations"][rid]
                    endpoints = (rel.get("source"), rel.get("target"))
                    if any(sid not in index["entities"] for sid in endpoints):
                        reasons.add("unresolved_endpoints")
                        continue
                    extra = list(dict.fromkeys(sid for sid in endpoints if sid not in levels))
                    if len(levels) + len(extra) > max_nodes:
                        reasons.add("max_nodes")
                        continue
                    if add_edge(self._graph_edge(rid)):
                        seen_edges.add(rid)
                        for sid in extra:
                            levels[sid] = level + 1
                            queue.append(sid)
            totals = {"reached_nodes": len(levels), "reached_edges": len(edges)}
        def visible (sid):
            kind = self.entities[sid]["kind"]
            return (show_functions or kind not in ("function", "inline_instance")) and (show_variables or kind not in ("variable", "parameter", "linker_symbol", "unresolved_symbol"))
        visible_ids = {sid for sid in levels if visible(sid)}
        visible_edges = [e for e in edges if e["source"] in visible_ids and e["target"] in visible_ids]
        owner = self.module_id(root) if root else None
        nodes = [{**self.brief(sid), "depth": level, "is_focus": sid == root,
                  "external": bool(owner and self.module_id(sid) != owner)} for sid, level in levels.items() if sid in visible_ids]
        return {"status": "ok", "view": view, "root": root, "focus": self.brief(root) if root else None,
                "direction": direction, "requested_depth": depth, "depth_applies": view == "symbol",
                "nodes": nodes, "edges": visible_edges, "totals": totals,
                "visibility": {"show_functions": show_functions, "show_variables": show_variables,
                               "hidden_nodes": len(levels) - len(nodes), "hidden_edges": len(edges) - len(visible_edges),
                               "applied_after_traversal": True},
                "limits": {"max_nodes": max_nodes, "max_edges": max_edges, "member_limit": member_limit},
                "truncated": bool(reasons), "truncation_reasons": sorted(reasons),
                "continuation": {"global": "Page search(query='', kind='module') and connections; drill into a module UUID.",
                                 "module": "Page members(root) and references(root); drill into a symbol UUID.",
                                 "symbol": "Page references(root), then relation(UUID) for sites; expand another root."}[view],
                "interpretation": "GUI graph semantics without layout. References are not confirmed calls. Ownership and aggregated edges have no relation UUID."}

    def _navigation_result (self, result):
        return {**result, "navigation": {"history_index": self._history_index, "history_length": len(self._history),
                                        "can_go_back": self._history_index > 0,
                                        "can_go_forward": self._history_index + 1 < len(self._history),
                                        "current_request": self._history[self._history_index] if self._history_index >= 0 else None,
                                        "scope": "current server-studio / Python session only"}}

    def navigate (self, query = None, view = "auto", depth = 1, direction = "both", max_nodes = 150, max_edges = 1000, member_limit = 75, show_functions = True, show_variables = True):
        params = {key: value for key, value in locals().items() if key != "self"}
        result = self.graph(**params)
        if result.get("status") != "ok":
            return self._navigation_result(result)
        params.update(query=result["root"], view=result["view"])
        self._history = self._history[:self._history_index + 1] + [params]
        self._history_index = len(self._history) - 1
        return self._navigation_result(result)

    def current (self):
        if self._history_index < 0:
            return self._navigation_result({"status": "no_navigation_history", "hint": "Use navigate in this server-studio or retained Python session."})
        return self._navigation_result(self.graph(**self._history[self._history_index]))

    def back (self):
        if self._history_index <= 0:
            return self._navigation_result({"status": "no_previous_view"})
        self._history_index -= 1
        return self.current()

    def forward (self):
        if self._history_index + 1 >= len(self._history):
            return self._navigation_result({"status": "no_next_view"})
        self._history_index += 1
        return self.current()

    def relation (self, query, limit = 30, offset = 0):
        if query not in self.data["relations"]:
            return {"status": "not_found", "query": query}
        result = self.relation_summary(query, 0)
        sites = self.data["relations"][query].get("sites", [])
        limit, offset = max(1, min(int(limit), 200)), max(0, int(offset))
        result["sites"] = [{**site, "source_location": self.location(site.get("source_location"))} for site in sites[offset:offset + limit]]
        result.update(status="ok", offset=offset, next_offset=offset + len(result["sites"]) if offset + len(result["sites"]) < len(sites) else None)
        return result

    def dependencies (self, query, module = None, direction = "outgoing", depth = 1, max_nodes = 100, max_edges = 200,
                      functions_only = False, confirmed_calls_only = False, include_types = False):
        sid, error = self.resolve(query, module)
        if error:
            return error
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction must be incoming, outgoing or both")
        depth = max(0, min(int(depth), 20))
        max_nodes, max_edges = max(1, min(int(max_nodes), 2000)), max(1, min(int(max_edges), 5000))
        module_mode = self.entities[sid]["kind"] == "module"
        inc = self.module_incoming if module_mode else self.incoming
        out = self.module_outgoing if module_mode else self.outgoing
        queue, visited, edges, edge_ids = deque([(sid, 0, [sid])]), {sid: 0}, [], set()
        truncated = False
        while queue:
            current, level, ancestry = queue.popleft()
            if level >= depth:
                continue
            adjacent = ([(rid, "outgoing") for rid in out[current]] if direction in ("outgoing", "both") else [])
            if not module_mode and direction in ("outgoing", "both"):
                adjacent += [(rid, "outgoing") for rid in self.inline_outgoing[current]]
            adjacent += [(rid, "incoming") for rid in inc[current]] if direction in ("incoming", "both") else []
            for rid, way in adjacent:
                rel = self.data["relations"][rid]
                if rel["relation"] not in ({"references", "calls", "has_type", "returns_type", "uses_type"} if include_types else {"references", "calls"}):
                    continue
                if confirmed_calls_only and rel["relation"] != "calls" and not any(s.get("dispatch") in ("direct_call", "indirect_call") for s in rel.get("sites", [])):
                    continue
                source, target = rel.get("source"), rel.get("target")
                physical_source = source
                if way == "outgoing" and rid in self.inline_outgoing.get(current, []):
                    source = current
                if functions_only and any(self.entities.get(x, {}).get("kind") not in ("function", "inline_instance") for x in (source, target)):
                    continue
                if module_mode:
                    source = self.module_id(source) or rel.get("source_module_id")
                    target = self.module_id(target)
                neighbour = target if way == "outgoing" else source
                if neighbour in self.entities and neighbour not in visited and len(visited) >= max_nodes:
                    truncated = True
                    continue
                if rid not in edge_ids:
                    if len(edges) >= max_edges:
                        truncated = True
                        continue
                    edges.append({"id": rid, "source": source, "target": target, "relation": rel["relation"],
                                  "physical_source": physical_source,
                                  "resolution": rel.get("resolution"), "site_count": len(rel.get("sites", [])),
                                  "cycle_to_ancestor": neighbour in ancestry, "source_location":
                                  self.location(next((s["source_location"] for s in rel.get("sites", []) if s.get("source_location")), None))})
                    edge_ids.add(rid)
                if neighbour in self.entities and neighbour not in visited:
                    if len(visited) >= max_nodes:
                        truncated = True
                        continue
                    visited[neighbour] = level + 1
                    queue.append((neighbour, level + 1, ancestry + [neighbour]))
        return {"status": "ok", "root": sid, "direction": direction, "requested_depth": depth,
                "interpretation": "confirmed_calls" if confirmed_calls_only else "potential_dependencies_from_recorded_references",
                "nodes": [{**self.brief(key), "depth": level} for key, level in visited.items()], "edges": edges,
                "truncated": truncated, "warning": "No edge does not prove absence of dependency; see build coverage."}

    def paths (self, query, target, depth = 5):
        start, error = self.resolve(query)
        if error:
            return error
        goal, error = self.resolve(target)
        if error:
            return error
        graph = self.dependencies(start, depth=depth, max_nodes=2000, max_edges=5000)
        adj = defaultdict(list)
        for edge in graph["edges"]:
            if edge["target"]:
                adj[edge["source"]].append((edge["target"], edge["id"]))
        queue, seen = deque([(start, [start], [])]), {start}
        while queue:
            node, path, relations = queue.popleft()
            if node == goal:
                return {"status": "ok", "path": [self.brief(s) for s in path], "relation_ids": relations,
                        "interpretation": "shortest recorded reference path; not a proven execution path"}
            for other, rid in adj[node]:
                if other not in seen:
                    seen.add(other)
                    queue.append((other, path + [other], relations + [rid]))
        return {"status": "not_found_within_limits", "depth": depth, "truncated": graph["truncated"]}

    def source (self, query = None, file_id = None, line = None, radius = 8):
        if query:
            sid, error = self.resolve(query)
            if error:
                return error
            location = self.entities[sid].get("definition") or self.entities[sid].get("declaration_location") or {}
            file_id, line = location.get("file_id"), line or location.get("line")
        if file_id not in self.data["files"] or not line:
            return {"status": "no_source_location"}
        file = self.data["files"][file_id]
        path = self.local_path(file)
        if path is None:
            return {"status": "source_root_required_or_unmapped", "location": self.location({"file_id": file_id, "line": line})}
        if not path.is_file():
            return {"status": "source_not_found", "path": str(path)}
        if path.stat().st_size > 20 * 1024 * 1024:
            return {"status": "source_too_large", "path": str(path)}
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        line, radius = int(line), max(0, min(int(radius), 100))
        if line < 1 or line > len(lines):
            return {"status": "line_out_of_range", "line_count": len(lines), "requested_line": line}
        low, high = max(1, line - radius), min(len(lines), line + radius)
        return {"status": "ok", "path": str(path), "source_revision_verified": False,
                "lines": [{"line": i, "text": lines[i - 1]} for i in range(low, high + 1)]}

    def query (self, method, **params):
        if method == "stats":
            if params:
                raise TypeError("stats accepts no method parameters")
            return {"retriever_version": RETRIEVER_VERSION, "build": self.data["build"], "coverage": self.data["coverage"]}
        if method == "dependents":
            return self.dependencies(**{**params, "direction": "incoming"})
        if method == "function_chain":
            return self.dependencies(**{**params, "functions_only": True})
        if method not in TOOLS:
            raise ValueError(f"Unknown method: {method}")
        return getattr(self, method)(**params)


def bounded_response (result, max_chars = 20000):
    max_chars = max(1000, min(int(max_chars), 2000000))
    result = json.loads(json.dumps(result, ensure_ascii=False))
    initial = len(json.dumps(result, ensure_ascii=False))
    if initial <= max_chars:
        return result
    result.update(truncated=True, truncation_reason="max_chars", original_chars=initial)
    while len(json.dumps(result, ensure_ascii=False)) > max_chars:
        lists = []
        def collect (item):
            if isinstance(item, dict):
                for value in item.values():
                    collect(value)
            elif isinstance(item, list):
                if len(item) > 1:
                    lists.append(item)
                for value in item:
                    collect(value)
        collect(result)
        if not lists:
            return {"status": "response_too_large", "truncated": True, "max_chars": max_chars,
                    "hint": "Increase max_chars or request fewer fields/sites/depth."}
        largest = max(lists, key=lambda x: len(json.dumps(x, ensure_ascii=False)))
        del largest[max(1, len(largest) // 2):]
    if "results" in result and "offset" in result:
        end = result["offset"] + len(result["results"])
        result["next_offset"] = end if end < result.get("total", end) else None
    if "sites" in result and "offset" in result:
        end = result["offset"] + len(result["sites"])
        result["next_offset"] = end if end < result.get("site_count", end) else None
    if "nodes" in result and "edges" in result:
        ids = {node["id"] for node in result["nodes"]}
        result["edges"] = [edge for edge in result["edges"] if edge.get("source") in ids and edge.get("target") in ids]
    return result


def describe_tools ():
    descriptions = {
        "query": "Entity UUID or exact name; search additionally matches substrings. For relation, supply a relation UUID. Resolve ambiguous names first.",
        "module": "Owner module name or UUID. Search uses substring matching; other methods use exact module identity.",
        "kind": "Entity kind. Only search and members accept this filter. members(kind='file') pages a module's source files.",
        "peer": "Optional other endpoint UUID/name for references; a module peer matches references involving its symbols.",
        "direction": "incoming follows referrers; outgoing follows referenced objects; both follows either. Edge arrows retain recorded direction.",
        "depth": "Maximum reference hops; root is depth 0. Only symbol graph views apply depth/direction. This is not lexical nesting.",
        "limit": "Maximum items per page or preview; context applies this independently to several previews.",
        "offset": "Zero-based page offset. Continue with the returned next_offset.",
        "sites_limit": "Number of reference sites previewed per relation; use relation to page all sites.",
        "max_nodes": "Maximum nodes retained during graph traversal, before visibility filters.",
        "max_edges": "Maximum graph edges before visibility filters; truncated results are not complete.",
        "functions_only": "Traversal filter that excludes non-function intermediates; differs from GUI visibility filtering.",
        "confirmed_calls_only": "Require explicit call evidence. This parser does not currently extract confirmed calls.",
        "include_types": "Also traverse recorded type relations where supported; does not create module type graphs.",
        "target": "Destination entity UUID or exact name for an outgoing reference path.",
        "file_id": "File UUID from a source location, not a filesystem path.",
        "line": "One-based source line; pair with file_id to inspect an actual reference site.",
        "radius": "Maximum source lines before and after the selected line; source root is configured on the process.",
        "view": "auto chooses global with no query, module for a module, otherwise symbol. module plus a symbol opens its owner.",
        "member_limit": "Initial owned symbols included in a module graph; use members pagination for all members.",
        "show_functions": "Display functions/inline instances after traversal. Hidden intermediates still participate in traversal.",
        "show_variables": "Display variables/parameters/linker and unresolved symbols after traversal. Modules remain visible."
    }
    integer_names = {"depth", "limit", "offset", "sites_limit", "max_nodes", "max_edges", "line", "radius", "member_limit"}
    boolean_names = {"functions_only", "confirmed_calls_only", "include_types", "show_functions", "show_variables"}
    tools = []
    for name, description in TOOLS.items():
        method = "dependencies" if name in ("dependents", "function_chain") else name
        parameters = [] if name == "stats" else list(inspect.signature(getattr(KnowledgeBase, method)).parameters.items())[1:]
        properties, required = {}, []
        for key, parameter in parameters:
            type_name = "integer" if key in integer_names else "boolean" if key in boolean_names else "string"
            item = {"type": [type_name, "null"] if parameter.default is None else type_name, "description": descriptions[key]}
            if parameter.default is inspect.Parameter.empty:
                required.append(key)
            else:
                item["default"] = parameter.default
            if key == "direction":
                item["enum"] = ["incoming", "outgoing", "both"]
                if name == "dependents":
                    item.update(default="incoming", enum=["incoming"])
            if key == "functions_only" and name == "function_chain":
                item.update(default=True, const=True)
            if key == "view":
                item["enum"] = ["auto", "global", "module", "symbol"]
            properties[key] = item
        tools.append({"name": name, "description": description,
                      "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}})
    return {"retriever_version": RETRIEVER_VERSION, "transport": "Shared command CLI (--server-studio) or one-shot CLI; not JSONL or MCP",
            "session": "navigate/back/forward/current retain history within one server-studio or Python session; successful reload clears history",
            "tools": tools}


def add_query_arguments (parser):
    """Share method syntax between one-shot invocations and the live prompt."""
    parser.add_argument("--max-chars", type=int, help="Response character budget; default 20000, clamped to 1000..2000000")
    parser.add_argument("method", nargs="?", choices=list(TOOLS))
    parser.add_argument("query", nargs="?")
    parser.add_argument("--module")
    parser.add_argument("--peer")
    parser.add_argument("--kind")
    parser.add_argument("--depth", type=int)
    parser.add_argument("--direction", choices=["incoming", "outgoing", "both"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int)
    parser.add_argument("--max-nodes", type=int)
    parser.add_argument("--max-edges", type=int)
    parser.add_argument("--sites-limit", type=int)
    parser.add_argument("--target")
    parser.add_argument("--file-id")
    parser.add_argument("--line", type=int)
    parser.add_argument("--radius", type=int)
    parser.add_argument("--view", choices=["auto", "global", "module", "symbol"])
    parser.add_argument("--member-limit", type=int)
    parser.add_argument("--show-functions", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--show-variables", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--functions-only", action="store_true", default=None)
    parser.add_argument("--confirmed-calls-only", action="store_true", default=None)
    parser.add_argument("--include-types", action="store_true", default=None)


def query_parameters (args):
    excluded = {"kb", "source_root", "server_studio", "list_tools", "describe_tools", "max_chars", "method"}
    return {key: value for key, value in vars(args).items() if key not in excluded and value is not None}


def split_command (line):
    """Honor single/double quotes, empty arguments and literal Windows backslashes.

    Commands never pass through an operating-system shell. There is no variable
    expansion, redirection, piping or backslash escape processing at this prompt.
    """
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


class PromptArgumentParser(argparse.ArgumentParser):
    def error (self, message):
        raise ValueError(message)


class HumanConsole:
    """ANSI decoration for the shared command console.

    Query results are never modified. Color is automatically disabled when
    stdout is redirected or when the NO_COLOR environment variable is set.
    """

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"

    BRIGHT_BLUE = "\033[94m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"

    JSON_TOKEN = re.compile(
        r'("(?:\\.|[^"\\])*")(?=\s*:)|'
        r'("(?:\\.|[^"\\])*")|'
        r'\b(true|false)\b|'
        r'\b(null)\b|'
        r'(?<![\w"])(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)(?![\w"])'
    )

    @classmethod
    def enabled (cls):
        return sys.stdout.isatty() and "NO_COLOR" not in os.environ

    @classmethod
    def paint (cls, text, *styles):
        if not cls.enabled():
            return str(text)
        return "".join(styles) + str(text) + cls.RESET

    @classmethod
    def heading (cls, text):
        return cls.paint(text, cls.BOLD, cls.BRIGHT_CYAN)

    @classmethod
    def label (cls, text):
        return cls.paint(text, cls.BOLD, cls.CYAN)

    @classmethod
    def success (cls, text):
        return cls.paint(text, cls.BOLD, cls.GREEN)

    @classmethod
    def warning (cls, text):
        return cls.paint(text, cls.BOLD, cls.YELLOW)

    @classmethod
    def error (cls, text):
        return cls.paint(text, cls.BOLD, cls.RED)

    @classmethod
    def muted (cls, text):
        return cls.paint(text, cls.DIM)

    @classmethod
    def command (cls, text):
        return cls.paint(text, cls.BOLD, cls.BRIGHT_BLUE)

    @classmethod
    def json (cls, result):
        raw = json.dumps(result, ensure_ascii=False, indent=2)
        if not cls.enabled():
            return raw

        def color_token (match):
            key, string, boolean, null, number = match.groups()
            if key is not None:
                return cls.paint(key, cls.CYAN)
            if string is not None:
                return cls.paint(string, cls.GREEN)
            if boolean is not None:
                return cls.paint(boolean, cls.MAGENTA)
            if null is not None:
                return cls.paint(null, cls.DIM)
            return cls.paint(number, cls.YELLOW)

        return cls.JSON_TOKEN.sub(color_token, raw)

    @classmethod
    def prompt (cls):
        return cls.paint("xref_if", cls.BOLD, cls.BRIGHT_CYAN) + cls.paint("> ", cls.BOLD, cls.BRIGHT_WHITE)

    @classmethod
    def clear_screen (cls):
        if sys.stdout.isatty():
            print("\033[2J\033[H", end="", flush=True)
        else:
            print(flush=True)

    @classmethod
    def rule (cls, width = 72):
        return cls.muted("-" * max(24, width))



class HumanLineEditor:
    """Standard-library line editor for the shared command console.

    Windows uses msvcrt so TAB completion works without third-party packages.
    Other platforms use readline when available and fall back to input().
    """

    def __init__ (self, commands):
        self.commands = sorted(set(commands))
        self.history = []
        self.history_limit = 200

    def _matches (self, prefix):
        return [command for command in self.commands if command.startswith(prefix)]

    def _complete_command (self, text, cursor):
        before = text[:cursor]
        if not before or before[:1].isspace() or any(char.isspace() for char in before):
            return text, cursor, []

        matches = self._matches(before)
        if not matches:
            return text, cursor, []

        common = os.path.commonprefix(matches)
        if len(common) > len(before):
            text = common + text[cursor:]
            cursor = len(common)

        if len(matches) == 1:
            if cursor == len(text) or not text[cursor:cursor + 1].isspace():
                text = text[:cursor] + " " + text[cursor:]
                cursor += 1

        return text, cursor, matches

    @staticmethod
    def _write_candidates (matches):
        print()
        print(HumanConsole.label("Matches") + " : " +
              "  ".join(HumanConsole.command(item) for item in matches), flush=True)

    def _remember (self, line):
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            if len(self.history) > self.history_limit:
                del self.history[:-self.history_limit]

    def _read_windows (self, prompt):
        import msvcrt

        buffer = []
        cursor = 0
        rendered_length = 0
        history_index = len(self.history)
        saved_current = ""
        last_tab_prefix = None

        def redraw ():
            nonlocal rendered_length
            current = "".join(buffer)
            erase = max(0, rendered_length - len(current))

            sys.stdout.write("\r" + prompt + current + " " * erase)

            move_left = erase + len(current) - cursor
            if move_left > 0:
                sys.stdout.write(f"\033[{move_left}D")

            sys.stdout.flush()
            rendered_length = len(current)

        sys.stdout.write(prompt)
        sys.stdout.flush()

        while True:
            char = msvcrt.getwch()

            if char == "\r":
                line = "".join(buffer)
                print()
                self._remember(line)
                return line

            if char == "\x03":
                print("^C")
                raise KeyboardInterrupt

            if char == "\x08":
                if cursor > 0:
                    del buffer[cursor - 1]
                    cursor -= 1
                    redraw()
                last_tab_prefix = None
                continue

            if char == "\t":
                current = "".join(buffer)
                prefix = current[:cursor]
                completed, new_cursor, matches = self._complete_command(current, cursor)

                if completed != current:
                    buffer[:] = list(completed)
                    cursor = new_cursor
                    redraw()
                    last_tab_prefix = prefix
                elif len(matches) > 1 and last_tab_prefix == prefix:
                    self._write_candidates(matches)
                    rendered_length = 0
                    redraw()
                    last_tab_prefix = None
                else:
                    last_tab_prefix = prefix if matches else None

                continue

            if char in ("\x00", "\xe0"):
                key = msvcrt.getwch()

                if key == "K" and cursor > 0:
                    cursor -= 1
                    redraw()

                elif key == "M" and cursor < len(buffer):
                    cursor += 1
                    redraw()

                elif key == "G":
                    cursor = 0
                    redraw()

                elif key == "O":
                    cursor = len(buffer)
                    redraw()

                elif key == "S" and cursor < len(buffer):
                    del buffer[cursor]
                    redraw()

                elif key == "H" and self.history:
                    if history_index == len(self.history):
                        saved_current = "".join(buffer)

                    history_index = max(0, history_index - 1)
                    buffer[:] = list(self.history[history_index])
                    cursor = len(buffer)
                    redraw()

                elif key == "P" and self.history:
                    if history_index < len(self.history) - 1:
                        history_index += 1
                        buffer[:] = list(self.history[history_index])
                    else:
                        history_index = len(self.history)
                        buffer[:] = list(saved_current)

                    cursor = len(buffer)
                    redraw()

                last_tab_prefix = None
                continue

            if char >= " " and char != "\x7f":
                buffer[cursor:cursor] = [char]
                cursor += 1
                redraw()
                last_tab_prefix = None

    def _read_readline (self, prompt):
        try:
            import readline
        except ImportError:
            return input(prompt)

        old_completer = readline.get_completer()
        old_delims = readline.get_completer_delims()

        def completer (value, state):
            matches = self._matches(value) if readline.get_begidx() == 0 else []
            return (matches[state] + " ") if state < len(matches) else None

        try:
            readline.set_completer_delims(" \t\n")
            readline.set_completer(completer)
            readline.parse_and_bind("tab: complete")
            line = input(prompt)
            self._remember(line)
            return line
        finally:
            readline.set_completer(old_completer)
            readline.set_completer_delims(old_delims)

    def read (self, prompt):
        if os.name == "nt" and sys.stdin.isatty() and sys.stdout.isatty():
            return self._read_windows(prompt)
        return self._read_readline(prompt)


class InteractiveSession:
    """Shared human/agent command loop over one retained KnowledgeBase instance."""

    BUILTINS = {
        "help": "help [COMMAND] - List commands or show a command's parameters.",
        "session": "session - Show the loaded KB, source root, budget and navigation state.",
        "set": "set source-root PATH|none  /  set max-chars INTEGER - Change a session setting.",
        "reload": "reload [KB_PATH] - Load a new snapshot; clear history only on success.",
        "clear": "clear - Clear the terminal display; KB and navigation state are preserved.",
        "exit": "exit / quit - Close the process and release its in-memory KB.",
        "quit": "quit / exit - Close the process and release its in-memory KB."
    }

    def __init__ (self, kb, max_chars = 20000):
        self.kb = kb
        self.max_chars = max(1000, min(int(max_chars), 2000000))
        self.parser = PromptArgumentParser(prog="xref_if", description="Type help COMMAND for command-specific options.")
        add_query_arguments(self.parser)
        self.line_editor = HumanLineEditor(list(TOOLS) + list(self.BUILTINS))

    @staticmethod
    def print_json (result):
        print(HumanConsole.json(result), flush=True)

    @staticmethod
    def print_section (title):
        print()
        print(HumanConsole.heading(title))
        print(HumanConsole.rule())

    def show_help (self, command = None):
        if command in self.BUILTINS:
            print(HumanConsole.heading(command))
            print(HumanConsole.rule(48))
            print(self.BUILTINS[command])
            return

        if command is None:
            print(HumanConsole.heading("GHS Xref Retriever - Command Reference"))
            print(HumanConsole.rule())
            print("Enter a command below without a python prefix. Results are JSON.\n")

            print(HumanConsole.label("Query commands"))
            for name, description in TOOLS.items():
                print(f"  {HumanConsole.command(f'{name:16}')} {description}")

            print()
            print(HumanConsole.label("Session commands"))
            for name, description in self.BUILTINS.items():
                if name != "quit":
                    command_name = description.split(" - ", 1)[0]
                    detail = description.split(" - ", 1)[1] if " - " in description else ""
                    print(f"  {HumanConsole.command(f'{command_name:28}')} {detail}")

            print()
            print(HumanConsole.label("Examples"))
            examples = (
                "search Nm_ChannelConfig --kind variable",
                "context Nm_ChannelConfig --module Nm_Cfg",
                "navigate Nm_ChannelConfig --depth 2",
                "navigate --view global",
                "back",
                "help references"
            )
            for example in examples:
                print(f"  {HumanConsole.muted('$')} {HumanConsole.command(example)}")

            print()
            print(HumanConsole.muted("Use quotes for paths/names with spaces. Backslashes are literal."))
            print(HumanConsole.muted("TAB completes command names; press TAB twice to list ambiguous matches."))
            print(HumanConsole.muted("Up/Down browse command history; Ctrl+C cancels the current command."))
            print(HumanConsole.muted("Humans and agents use the same commands. This prompt is not PowerShell or JSONL."))
            return

        tool = next((item for item in describe_tools()["tools"] if item["name"] == command), None)
        if tool is None:
            raise ValueError(f"Unknown command: {command}. Type help to list commands.")

        schema = tool["input_schema"]
        required = schema["required"]
        query_hint = " QUERY" if "query" in required else " [QUERY]" if "query" in schema["properties"] else ""

        print(HumanConsole.heading(f"Help: {command}"))
        print(HumanConsole.rule())
        print(f"{HumanConsole.label('Usage')} : {HumanConsole.command(command + query_hint + ' [options]')}")
        print(f"{HumanConsole.label('About')} : {tool['description']}\n")

        for name, spec in schema["properties"].items():
            label = "QUERY" if name == "query" else "--" + name.replace("_", "-")
            if name in ("show_functions", "show_variables"):
                label += " / --no-" + name.replace("_", "-")
            detail = "required" if name in required else "default=" + json.dumps(spec.get("default"))
            choices = "; choices=" + "/".join(spec["enum"]) if "enum" in spec else ""
            print(f"  {HumanConsole.command(label)} {HumanConsole.muted('(' + detail + choices + ')')}")
            print(f"    {spec['description']}")

        print(f"  {HumanConsole.command('--max-chars INTEGER')} {HumanConsole.muted(f'(session default={self.max_chars})')}")
        print("    Override the response budget for this command only.")

    def session_info (self):
        return {"retriever_version": RETRIEVER_VERSION, "mode": "server-studio", "kb": str(self.kb.path),
                "build_id": self.kb.data.get("build", {}).get("id"),
                "source_root": str(self.kb.source_root) if self.kb.source_root else None,
                "max_chars": self.max_chars,
                "counts": {table: len(self.kb.data[table]) for table in ("modules", "symbols", "relations")},
                "navigation": {"history_index": self.kb._history_index, "history_length": len(self.kb._history)},
                "snapshot": "Kept in memory until reload or exit; disk changes are not loaded automatically."}

    def execute (self, line):
        """Execute a single prompt command; return False only for exit/quit."""
        tokens = split_command(line)
        if not tokens:
            return True
        command, arguments = tokens[0], tokens[1:]

        if command in ("exit", "quit"):
            if arguments:
                raise ValueError(f"Usage: {command}")
            return False

        if command in ("help", "?", "--help", "-h"):
            if len(arguments) > 1:
                raise ValueError("Usage: help [COMMAND]")
            self.show_help(arguments[0] if arguments else None)

        elif command == "clear":
            if arguments:
                raise ValueError("Usage: clear")
            HumanConsole.clear_screen()

        elif command == "session":
            if arguments:
                raise ValueError("Usage: session")
            self.print_json(self.session_info())

        elif command == "set":
            if len(arguments) != 2 or arguments[0] not in ("source-root", "max-chars"):
                raise ValueError("Usage: set source-root PATH|none  /  set max-chars INTEGER")
            setting, value = arguments
            if setting == "source-root":
                path = None if value.lower() == "none" else Path(value).expanduser().resolve()
                if path is not None and not path.is_dir():
                    raise ValueError(f"Source folder does not exist: {path}")
                self.kb.source_root = path
            else:
                self.max_chars = max(1000, min(int(value), 2000000))
            self.print_json({"status": "ok", "setting": setting, "value": self.session_info()[setting.replace("-", "_")]})

        elif command == "reload":
            if len(arguments) > 1:
                raise ValueError("Usage: reload [KB_PATH]")
            path = Path(arguments[0]).expanduser().resolve() if arguments else self.kb.path
            print(f"{HumanConsole.label('Loading KB')} : {path}", flush=True)
            replacement = KnowledgeBase(path, self.kb.source_root)
            self.kb = replacement
            self.print_json({"status": "ok", "message": "KB reloaded; navigation history cleared.", **self.session_info()})

        elif command in TOOLS:
            if "--help" in arguments or "-h" in arguments:
                self.show_help(command)
                return True
            args = self.parser.parse_args(tokens)
            budget = args.max_chars if args.max_chars is not None else self.max_chars
            self.print_json(bounded_response(self.kb.query(args.method, **query_parameters(args)), budget))

        else:
            raise ValueError(f"Unknown command: {command}. Type help to list commands.")
        return True

    def run (self):
        module_count = len(self.kb.data["modules"])
        symbol_count = len(self.kb.data["symbols"])

        print()
        print(HumanConsole.rule())
        print(HumanConsole.heading(f"GHS Xref Retriever {RETRIEVER_VERSION}"))
        print(HumanConsole.muted("Shared human/agent command console (--server-studio)"))
        print(HumanConsole.rule())
        print(f"{HumanConsole.label('Ready')}   : {HumanConsole.success(f'{module_count} modules')} | "
              f"{HumanConsole.success(f'{symbol_count} symbols')}")
        print(f"{HumanConsole.label('Session')} : KB stays in memory until reload or exit.")
        print(f"{HumanConsole.label('Help')}    : type {HumanConsole.command('help')} for commands, "
              f"{HumanConsole.command('help COMMAND')} for options, or {HumanConsole.command('exit')} to quit.")
        print(f"{HumanConsole.label('Keys')}    : TAB autocomplete | Up/Down history | "
              f"{HumanConsole.command('clear')} clears screen")
        print(HumanConsole.rule())
        print()

        while True:
            try:
                if not self.execute(self.line_editor.read(HumanConsole.prompt())):
                    break
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                print(HumanConsole.warning("[CANCELLED]") +
                      " Command cancelled. Session remains open; type exit to quit.", flush=True)
            except (ValueError, KeyError, TypeError, OSError) as error:
                print()
                print(HumanConsole.error("[ERROR]") + f" {error}")
                print(HumanConsole.muted("Type help COMMAND for usage. Session remains open."), flush=True)

        print()
        print(HumanConsole.muted("Session closed."), flush=True)


def main ():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", default=str(DEFAULT_KB), help="KB path; defaults to knowledge_base.json beside this script")
    parser.add_argument("--source-root", help="Project checkout root for optional source reads")
    parser.add_argument("--server-studio", action="store_true", help="Start the shared human/agent prompt (the default when no method is given)")
    parser.add_argument("--list-tools", action="store_true")
    parser.add_argument("--describe-tools", action="store_true")
    add_query_arguments(parser)
    args = parser.parse_args()
    if args.describe_tools:
        print(json.dumps(describe_tools(), ensure_ascii=False, indent=2))
        return
    if args.list_tools:
        print(json.dumps(TOOLS, indent=2))
        return
    if args.server_studio and args.method is not None:
        parser.error("--server-studio cannot be combined with a one-shot method")
    if args.method is None and query_parameters(args):
        parser.error("Method options require a method; enter the command after the server-studio prompt starts")
    max_chars = args.max_chars if args.max_chars is not None else 20000
    try:
        if args.method is None:
            print(f"{HumanConsole.label('Loading KB')} : {Path(args.kb).expanduser().resolve()}", flush=True)
            InteractiveSession(KnowledgeBase(args.kb, args.source_root), max_chars).run()
            return
        kb = KnowledgeBase(args.kb, args.source_root)
        result = kb.query(args.method, **query_parameters(args))
        print(json.dumps(bounded_response(result, max_chars), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
