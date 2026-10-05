#!/usr/bin/env python3
"""Generate a self-contained interactive HTML graph from knowledge_base.json.

Python standard library only. Keep ghs_xref_retriever.py beside this file.
The HTML runs offline, opens VS Code at file:line, and can show local source
after the user selects a source folder. Source is never uploaded to a server.
Default input: knowledge_base.json beside this script.
Default output: knowledge_graph.html beside this script, regardless of the CWD
or the input KB location. --output explicitly overrides this destination.
Rendering caches geometry and separates hover highlights from the base scene.
Input schema, graph contents, traversal limits and source navigation are unchanged.
"""

import argparse
import json
from pathlib import Path

from ghs_xref_retriever import DEFAULT_KB, KnowledgeBase

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_HTML = SCRIPT_DIR / "knowledge_graph.html"

def visual_data (kb, source_root = ""):
    relations = {}
    endpoint_ids = set()
    for rid, rel in kb.data["relations"].items():
        if rel["relation"] != "references":
            continue
        relations[rid] = {key: rel.get(key) for key in ("source", "target", "source_module_id", "target_raw_name", "resolution", "source_candidates", "target_candidates")}
        relations[rid]["sites"] = [{key: site.get(key) for key in ("reference_address", "source_location", "access", "dispatch", "inline_instance_ids")}
                                     for site in rel.get("sites", [])]
        endpoint_ids.update(s for s in (rel.get("source"), rel.get("target")) if s)
    symbols = {}
    for sid, value in kb.data["symbols"].items():
        if value.get("is_declaration") and sid not in endpoint_ids:
            continue
        symbols[sid] = {key: value.get(key) for key in ("kind", "name", "module_id", "parent_function_id", "scope_id", "definition",
            "address", "address_ranges", "declaration_location", "is_declaration")}
    modules = {mid: {key: module.get(key) for key in ("name", "object_name", "source_file_ids", "function_ids", "variable_ids")}
               for mid, module in kb.data["modules"].items()}
    return {"build": kb.data["build"], "coverage": kb.data["coverage"], "files": kb.data["files"],
            "modules": modules, "symbols": symbols, "relations": relations, "source_root": source_root}


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>GHS Knowledge Graph</title>
<style>
:root{color-scheme:dark;--bg:#07090d;--panel:#0d1118;--line:#243040;--text:#edf2f8;--muted:#91a0b4;--module:#77fdff;--fn:#31c936;--var:#efad50;--extfn:#f587ff;--extvar:#ef7878}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:13px system-ui,"Segoe UI",sans-serif;overflow:hidden}button,input,select{font:inherit}button,.action{background:#151e2d;color:var(--text);border:1px solid #334259;border-radius:7px;padding:7px 10px;cursor:pointer;text-decoration:none;display:inline-block}button:hover,.action:hover{background:#233047}button:disabled{opacity:.4;cursor:default}input,select{background:#111926;border:1px solid #344257;border-radius:7px;color:var(--text);padding:8px}header{height:66px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:20px;padding:0 20px;background:#0c1017}h1{font-size:18px;margin:0}header small,.muted{color:var(--muted)}header small{display:block;margin-top:4px}#counts{margin-left:auto;color:var(--muted)}#stage{position:absolute;inset:66px 400px 0 0}canvas{width:100%;height:100%;touch-action:none;cursor:grab}#toolbar{position:absolute;top:14px;left:14px;right:14px;display:flex;gap:7px;align-items:start;z-index:3}#searchBox{position:relative;flex:1;max-width:440px}#search{width:100%}#results{position:absolute;top:40px;left:0;right:0;max-height:420px;overflow:auto;background:#101722;border:1px solid var(--line);border-radius:8px;display:none}.result{display:block;text-align:left;width:100%;border:0;border-bottom:1px solid var(--line);border-radius:0;padding:10px}.result small{display:block;color:var(--muted);margin-top:3px;overflow-wrap:anywhere}#mode{position:absolute;top:64px;left:16px;color:var(--muted);font-size:12px;max-width:90%;pointer-events:none}#legend{position:absolute;bottom:18px;left:16px;background:#0e1520e8;border:1px solid var(--line);padding:12px;border-radius:9px;font-size:12px;line-height:1.8;pointer-events:none}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}aside{position:absolute;right:0;top:66px;bottom:0;width:400px;border-left:1px solid var(--line);background:var(--panel);overflow:auto;padding:16px}aside h2{font-size:16px;overflow-wrap:anywhere;line-height:1.4;margin:0 0 6px}aside h3{font-size:13px;margin:18px 0 8px}.controls{border-bottom:1px solid var(--line);padding-bottom:14px;margin-bottom:16px}.row{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.row label{display:flex;gap:5px;align-items:center}.muted{font-size:12px;line-height:1.5;overflow-wrap:anywhere}.mono{font-family:"JetBrains Mono",Consolas,monospace;font-size:11px;overflow-wrap:anywhere}.entry{border:1px solid var(--line);background:#101722;border-radius:7px;padding:9px;margin:7px 0;overflow-wrap:anywhere}.entry .name{background:none;border:0;padding:0;text-align:left;color:#b4c7ff;line-height:1.5}.loc{padding:4px 6px;font-size:11px;margin:3px 3px 0 0;background:#192737}.tag{display:inline-block;font-size:10px;padding:2px 5px;border:1px solid #36435c;border-radius:4px;color:var(--muted);margin:3px 3px 3px 0}.notice{font-size:12px;line-height:1.5;padding:9px;background:#182235;border-radius:6px;color:#c6d3e5}details{margin:10px 0}summary{cursor:pointer;color:#b7c9ed}details input[type=text]{width:100%;margin:8px 0}#sourceStatus{margin:7px 0}#sourcePane{position:absolute;left:0;right:400px;bottom:0;height:48vh;min-height:200px;z-index:5;background:#0d141e;border-top:2px solid #596bb0;display:none;box-shadow:0 -14px 44px #0006}#sourceHead{padding:10px 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}#sourceTitle{flex:1;overflow-wrap:anywhere;font-size:12px}#sourceBody{height:calc(100% - 52px);overflow:auto;font:12px/1.7 "JetBrains Mono",Consolas,monospace;padding:10px 0;white-space:pre}.sourceLine{display:flex;min-width:max-content}.sourceLine .number{min-width:64px;padding:0 12px;text-align:right;user-select:none;color:#8592a6;border-right:1px solid #25324a;margin-right:12px}.sourceLine.target{background:#263957;border-left:3px solid #b0c0ff}.sourceLine.target .number{min-width:61px;color:#fff}#tooltip{position:absolute;pointer-events:none;display:none;z-index:4;padding:8px;background:#162133;border:1px solid #425573;border-radius:6px;font-size:12px;max-width:450px;overflow-wrap:anywhere}#footer{position:absolute;bottom:14px;right:16px;color:#8794a9;font-size:11px;max-width:55%;text-align:right;pointer-events:none}.filepath{color:#b4c7ff}.edgeSample{display:inline-block;width:26px;margin-right:7px;vertical-align:middle;border-top:1.5px solid #9aaec8}.edgeSample.dashed{border-top-style:dashed}.edgeSample.dotted{border-top-style:dotted}.edgeSample.uncertain{height:2px;border:0;background:repeating-linear-gradient(to right,#9aaec8 0 8px,transparent 8px 12px,#9aaec8 12px 14px,transparent 14px 18px)}
@media(max-width:1050px){aside{width:340px}#stage,#sourcePane{right:340px}header{padding:0 12px}#counts{font-size:11px}#toolbar{gap:4px}#toolbar button{padding:8px}#legend{font-size:10px}}

/* Small composited transitions; no continuous animation or per-edge blur. */
header{background:linear-gradient(115deg,#10182a,#0c1017 64%)}
#stage{background:radial-gradient(ellipse at 40% 35%,#14203955,transparent 70%)}
#renderLayers{position:absolute;inset:0;overflow:hidden;contain:strict}
#graph,#highlight{position:absolute;display:block;transform-origin:0 0;will-change:transform}
#highlight{pointer-events:none;cursor:inherit}
button,.action{transition:background 130ms ease,border-color 130ms ease,transform 130ms ease}
button:hover:not(:disabled),.action:hover{border-color:#6d7eaa;transform:translateY(-1px)}
button:active:not(:disabled),.action:active{transform:translateY(0)}
button:focus-visible,input:focus-visible,select:focus-visible,.action:focus-visible{outline:2px solid #9aaaff;outline-offset:2px}
#searchBox{box-shadow:0 5px 18px #0003;border-radius:8px}
aside{scrollbar-gutter:stable;overscroll-behavior:contain}
.entry{content-visibility:auto;contain-intrinsic-size:auto 90px}
#tooltip{left:0;top:0;will-change:transform;box-shadow:0 4px 18px #0004}
#sourcePane{animation:sourceEnter 140ms ease-out}
@keyframes sourceEnter{from{opacity:.75;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}
@media(prefers-reduced-motion:reduce){button,.action{transition:none}#sourcePane{animation:none}}
</style></head><body>
<header><div><h1>GHS Knowledge Graph</h1><small>XC-CV · EEC · TSR Embedded CodeGraph - Module · Function · Variable</small></div><div id="counts"></div></header>
<main id="stage"><div id="renderLayers"><canvas id="graph" aria-label="Reference graph of modules and symbols"></canvas><canvas id="highlight" aria-hidden="true"></canvas></div>
<div id="toolbar"><button id="back" title="Back">←</button><button id="forward" title="Forward">→</button><div id="searchBox"><input id="search" placeholder="Search modules, functions, variables or UUIDs…" aria-label="Search"><div id="results"></div></div><button id="global">Global</button><button id="fit">Fit</button></div>
<div id="mode"></div><div id="legend"><span class="dot" style="background:var(--module)"></span>Module<br><span class="dot" style="background:var(--fn)"></span>Owned function<br><span class="dot" style="background:var(--var)"></span>Owned variable<br><span class="dot" style="background:var(--extfn)"></span>External function<br><span class="dot" style="background:var(--extvar)"></span>External variable<br><span class="edgeSample"></span>Function / module reference<br><span class="edgeSample dashed"></span>Data reference<br><span class="edgeSample dotted"></span>Ownership<br><span class="edgeSample uncertain"></span>Unresolved reference</div><div id="footer">Drag to pan · Scroll to zoom · Double-click to explore</div><div id="tooltip"></div></main>
<aside><div class="controls"><div class="row"><label>Depth <select id="depth"><option>1</option><option>2</option><option>3</option><option>4</option><option>6</option></select></label><select id="direction" aria-label="Traversal direction"><option value="both">Both directions</option><option value="outgoing">Dependencies</option><option value="incoming">Dependents</option></select></div><div class="row" style="margin-top:10px"><label><input type="checkbox" id="functions" checked>Functions</label><label><input type="checkbox" id="variables" checked>Variables</label></div>
<details id="sourceSettings"><summary>Connect local source</summary><p class="muted">Select your project root to view source inside the graph. Files stay on your computer. Use the same source revision as the build.</p><input id="folder" type="file" webkitdirectory multiple hidden><button id="chooseFolder">Choose source folder</button><div id="sourceStatus" class="muted">No source folder selected.</div><label class="muted" for="workspace">Current workspace path — for VS Code links</label><input id="workspace" type="text" placeholder="C:\sandboxes\...\UAH7HC_7"><p class="muted">Leave blank to use the recorded compiler path. Editor links require VS Code on this computer.</p></details></div>
<div id="detail"></div></aside>
<section id="sourcePane" aria-label="Source code"><div id="sourceHead"><div id="sourceTitle"></div><a id="editor" class="action" href="#">VS Code ↗</a><button id="closeSource">Close</button></div><div id="sourceBody"></div></section>
<script id="data" type="application/json">__KNOWLEDGE_DATA__</script>
<script>
'use strict';
const KB=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id);
// Release the large DOM text copy after parsing; the KB object remains intact.
document.getElementById('data').remove();
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const entities={...KB.modules,...KB.symbols}, incoming=new Map(), outgoing=new Map(), moduleRelations=new Map(), owned=new Map();
const add=(map,key,value)=>{if(key){if(!map.has(key))map.set(key,[]);map.get(key).push(value)}};
const moduleOf=id=>KB.modules[id]?id:KB.symbols[id]?.module_id;
for(const [id,s] of Object.entries(KB.symbols))add(owned,s.module_id,id);
for(const [id,r] of Object.entries(KB.relations)){add(outgoing,r.source,id);add(incoming,r.target,id);const a=moduleOf(r.source)||r.source_module_id,b=moduleOf(r.target);add(moduleRelations,a,id);if(b!==a)add(moduleRelations,b,id)}
const canvas=$('graph'), ctx=canvas.getContext('2d');let width=1,height=1,camera={x:0,y:0,z:1},nodes=[],edges=[],nodeMap=new Map(),view={kind:'global'},selected=null,hover=null,history=[],historyIndex=-1,drag=null,raf=false;
let localFiles=[],pendingSource=null;
const colors={module:'#77fdff',function:'#31c936',variable:'#efad50',external_function:'#f587ff',external_variable:'#ef7878'};
const kind=id=>KB.modules[id]?'module':KB.symbols[id]?.kind||'unknown';
const isFunction=id=>['function','inline_instance'].includes(kind(id));
const isVariable=id=>['variable','parameter','unresolved_symbol','linker_symbol'].includes(kind(id));
const name=id=>entities[id]?.name||id||'Unresolved';
const sourceLoc=id=>KB.symbols[id]?.definition||KB.symbols[id]?.declaration_location;
const relationLabel=r=>isFunction(r.target)?'Function reference':'Data reference';
function labelLocation(loc){const file=KB.files[loc?.file_id];return file?`${file.name}${loc.line?':'+loc.line:''}`:'No source line available'}
function locationButtons(loc){if(!loc?.file_id||!loc.line)return '<span class="tag">No source line available</span>';return `<button class="loc" data-file="${esc(loc.file_id)}" data-line="${loc.line}" data-column="${loc.column||1}">${esc(labelLocation(loc))} ↗</button>`}

let locationBinding = false, entityBinding = false, relationBinding = false;
function bindLocations () {
    if (locationBinding) return;
    locationBinding = true;
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-file]');
        if (button) showSource(button.dataset.file, Number(button.dataset.line), Number(button.dataset.column) || 1);
    });
}
function connectEntityButtons () {
    if (entityBinding) return;
    entityBinding = true;
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-entity]');
        if (!button || !entities[button.dataset.entity]) return;
        if (button.closest('#results')) closeSearch();
        navigate({kind: KB.modules[button.dataset.entity] ? 'module' : 'symbol', id: button.dataset.entity});
    });
}

function screen(n){return {x:width/2+(n.x-camera.x)*camera.z,y:height/2+(n.y-camera.y)*camera.z}}
function color(id){if(KB.modules[id])return colors.module;const mid=view.kind==='module'?view.id:moduleOf(view.id),external=mid&&moduleOf(id)!==mid;return isFunction(id)?(external?colors.external_function:colors.function):(external?colors.external_variable:colors.variable)}
function visible(id){return KB.modules[id]||(!isFunction(id)||$('functions').checked)&&(!isVariable(id)||$('variables').checked)}
// BEGIN CURVE_HELPERS
// Geometry is shared by drawing, arrow placement and pointer hit testing.
function assignEdgeLanes (relations) {
    const groups = new Map();
    for (const edge of relations) {
        const key = [edge.source, edge.target].sort().join('|');
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(edge);
    }
    for (const group of groups.values()) {
        group.forEach((edge, index) => {
            const lane = group.length === 1 ? 0.65 : index - (group.length - 1) / 2;
            edge.curveLane = lane === 0 ? 0.25 : lane;
        });
    }
}

function curveGeometry (edge, p, q, zoom = 1) {
    const dx = q.x - p.x, dy = q.y - p.y, distance = Math.hypot(dx, dy);
    const lane = edge.curveLane ?? 0.65;
    const scale = Math.max(0.6, Math.min(1.8, Math.sqrt(Math.max(0.08, zoom))));
    if (distance < 0.5) {
        const radius = (26 + Math.abs(lane) * 14) * scale;
        const side = lane < 0 ? 1 : -1;
        return {kind: 'cubic',
                p: {x: p.x - 5, y: p.y + side * 4},
                c1: {x: p.x - radius, y: p.y + side * radius * 2},
                c2: {x: p.x + radius, y: p.y + side * radius * 2},
                q: {x: p.x + 5, y: p.y + side * 4}};
    }
    const orientation = edge.source <= edge.target ? 1 : -1;
    const bend = orientation * Math.sign(lane || 1) *
                 (Math.min(90 * scale, Math.max(16 * scale, distance * 0.14)) + Math.abs(lane) * 18 * scale);
    return {kind: 'quadratic', p, q,
            c1: {x: (p.x + q.x) / 2 - dy / distance * bend,
                 y: (p.y + q.y) / 2 + dx / distance * bend}};
}

function curvePoint (curve, t) {
    const u = 1 - t, {p, q, c1, c2} = curve;
    if (curve.kind === 'cubic') {
        return {x: u ** 3 * p.x + 3 * u ** 2 * t * c1.x + 3 * u * t ** 2 * c2.x + t ** 3 * q.x,
                y: u ** 3 * p.y + 3 * u ** 2 * t * c1.y + 3 * u * t ** 2 * c2.y + t ** 3 * q.y};
    }
    return {x: u ** 2 * p.x + 2 * u * t * c1.x + t ** 2 * q.x,
            y: u ** 2 * p.y + 2 * u * t * c1.y + t ** 2 * q.y};
}

function curveDistance (curve, x, y) {
    const controls = curve.kind === 'cubic' ? [curve.p, curve.c1, curve.c2, curve.q] : [curve.p, curve.c1, curve.q];
    const length = controls.slice(1).reduce((sum, p, i) => sum + Math.hypot(p.x - controls[i].x, p.y - controls[i].y), 0);
    const steps = Math.max(24, Math.min(256, Math.ceil(length / 12)));
    let previous = curve.p, nearest = Infinity;
    for (let i = 1; i <= steps; i++) {
        const next = curvePoint(curve, i / steps);
        const dx = next.x - previous.x, dy = next.y - previous.y;
        const t = Math.max(0, Math.min(1, ((x - previous.x) * dx + (y - previous.y) * dy) / (dx * dx + dy * dy || 1)));
        nearest = Math.min(nearest, Math.hypot(x - previous.x - t * dx, y - previous.y - t * dy));
        previous = next;
    }
    return nearest;
}

function edgeDash (edge, dataReference = false) {
    if (edge.ownership) return [1, 5];
    if (edge.resolution && edge.resolution !== 'resolved') return [8, 4, 1, 4];
    return dataReference ? [7, 6] : [];
}
// END CURVE_HELPERS


function edgeGeometry (edge) {
    const cached = drawing?.revision === graphRevision ? drawing.byEdge.get(edge) : null;
    if (cached && canvasPose) {
        // Hit-test the curve actually displayed during a buffered pan/zoom.
        const scale = camera.z / drawing.zoom, dx = width / 2 - camera.x * camera.z, dy = height / 2 - camera.y * camera.z;
        const move = point => ({x: point.x * scale + dx, y: point.y * scale + dy});
        const geometry = cached.geometry;
        return {kind: geometry.kind, p: move(geometry.p), q: move(geometry.q), c1: move(geometry.c1),
                ...(geometry.c2 ? {c2: move(geometry.c2)} : {})};
    }
    const a = nodeMap.get(edge.source), b = nodeMap.get(edge.target);
    return a && b ? curveGeometry(edge, screen(a), screen(b), camera.z) : null;
}

// Keep the graph on one canvas and hover/selection on a second canvas.
// Pointer movement must not repaint every inactive edge in the scene.
const overlay = $('highlight'), overlayCtx = overlay.getContext('2d');
const functionFilter = $('functions'), variableFilter = $('variables');
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
let pixelRatio = 1, graphRevision = 0, drawing = null, baseStamp = '', overlayStamp = '';
let padding = 0, canvasPose = null, projectedStamp = '', refreshTimer = 0, forceRefresh = false;
let pointerSample = null, canvasBox = {left: 0, top: 0}, panelAnimation = null;

function requestDraw () {
    if (raf) return;
    raf = true;
    requestAnimationFrame(() => {
        raf = false;
        if (pointerSample) updateHover();
        draw();
    });
}

function resize () {
    const box = $('stage').getBoundingClientRect();
    width = Math.max(1, box.width);
    height = Math.max(1, box.height);
    // Limit backing-store cost on very high DPI displays, keeping CSS size intact.
    pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    padding = Math.round(Math.min(220, Math.max(120, Math.min(width, height) * 0.2)));
    for (const surface of [canvas, overlay]) {
        surface.width = Math.round((width + 2 * padding) * pixelRatio);
        surface.height = Math.round((height + 2 * padding) * pixelRatio);
        surface.style.width = (width + 2 * padding) + 'px';
        surface.style.height = (height + 2 * padding) + 'px';
        surface.style.left = surface.style.top = -padding + 'px';
    }
    canvasBox = {left: box.left, top: box.top};
    canvasPose = null;
    baseStamp = overlayStamp = '';
    requestDraw();
}

function animateDetail () {
    if (reducedMotion.matches || !$('detail').animate) return;
    panelAnimation?.cancel();
    panelAnimation = $('detail').animate([{opacity: 0.65, transform: 'translateY(4px)'},
                                         {opacity: 1, transform: 'translateY(0)'}],
                                        {duration: 140, easing: 'ease-out'});
}

function traceCurve (path, geometry) {
    const {p, q, c1, c2} = geometry;
    path.moveTo(p.x, p.y);
    if (geometry.kind === 'cubic') path.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, q.x, q.y);
    else path.quadraticCurveTo(c1.x, c1.y, q.x, q.y);
}

function traceArrow (path, geometry) {
    const {q} = geometry, tangent = geometry.kind === 'cubic' ? geometry.c2 : geometry.c1;
    const angle = Math.atan2(q.y - tangent.y, q.x - tangent.x);
    const inset = geometry.kind === 'cubic' ? 1 : 9;
    const x = q.x - inset * Math.cos(angle), y = q.y - inset * Math.sin(angle);
    path.moveTo(x, y);
    path.lineTo(x - 6 * Math.cos(angle - 0.45), y - 6 * Math.sin(angle - 0.45));
    path.lineTo(x - 6 * Math.cos(angle + 0.45), y - 6 * Math.sin(angle + 0.45));
    path.closePath();
}

function edgeBatch (batches, item, active) {
    const edge = item.edge;
    const stroke = view.kind === 'global' ? (active ? '#8397c7' : '#516387') : item.color;
    const alpha = active ? 0.9 : edge.ownership ? 0.24 : view.kind === 'global' ? 0.22 : 0.42;
    const lineWidth = active ? 1.65 : edge.ownership ? 0.8 : 1;
    const key = stroke + ':' + alpha + ':' + lineWidth + ':' + item.dash.join(',');
    if (!batches.has(key)) batches.set(key, {path: new Path2D(), arrows: new Path2D(),
                                          stroke, alpha, lineWidth, dash: item.dash, hasArrows: false});
    const batch = batches.get(key);
    traceCurve(batch.path, item.geometry);
    if (view.kind !== 'global' && !edge.ownership) {
        traceArrow(batch.arrows, item.geometry);
        batch.hasArrows = true;
    }
}

function prepareDrawing () {
    // Geometry is cached in zoomed world coordinates. Panning only translates it.
    const showFunctions = functionFilter.checked, showVariables = variableFilter.checked;
    const points = new Map(), list = [], batches = new Map(), dots = new Map();
    const adjacency = new Map(), byEdge = new Map();
    for (const node of nodes) {
        if ((!showFunctions && isFunction(node.id)) || (!showVariables && isVariable(node.id))) continue;
        const radius = KB.modules[node.id] ? 5 : 6, fill = color(node.id);
        let label = name(node.id);
        if (label.length > 51) label = label.slice(0, 48) + '…';
        const item = {id: node.id, x: node.x * camera.z, y: node.y * camera.z, radius, fill, label};
        points.set(node.id, item);
        list.push(item);
        const key = fill + ':' + radius;
        if (!dots.has(key)) dots.set(key, {path: new Path2D(), fill});
        const path = dots.get(key).path;
        path.moveTo(item.x + radius, item.y);
        path.arc(item.x, item.y, radius, 0, Math.PI * 2);
    }
    for (const edge of edges) {
        const p = points.get(edge.source), q = points.get(edge.target);
        if (!p || !q) continue;
        const item = {edge, geometry: curveGeometry(edge, p, q, camera.z),
                      color: color(edge.target), dash: edgeDash(edge, isVariable(edge.target))};
        byEdge.set(edge, item);
        edgeBatch(batches, item, false);
        add(adjacency, edge.source, item);
        if (edge.target !== edge.source) add(adjacency, edge.target, item);
    }
    drawing = {revision: graphRevision, zoom: camera.z, showFunctions, showVariables,
               points, nodes: list, batches, dots, adjacency, byEdge};
    baseStamp = overlayStamp = '';
}

function clearSurface (context) {
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, context.canvas.width, context.canvas.height);
    context.setTransform(pixelRatio, 0, 0, pixelRatio,
                         (padding + width / 2 - canvasPose.x * canvasPose.z) * pixelRatio,
                         (padding + height / 2 - canvasPose.y * canvasPose.z) * pixelRatio);
    context.lineCap = 'round';
    context.lineJoin = 'round';
}

function paintEdges (context, batches) {
    for (const batch of batches.values()) {
        context.strokeStyle = context.fillStyle = batch.stroke;
        context.globalAlpha = batch.alpha;
        context.lineWidth = batch.lineWidth;
        context.setLineDash(batch.dash);
        context.stroke(batch.path);
        if (batch.hasArrows) context.fill(batch.arrows);
    }
    context.globalAlpha = 1;
    context.setLineDash([]);
}

function inViewport (node) {
    const x = node.x + width / 2 - canvasPose.x * canvasPose.z;
    const y = node.y + height / 2 - canvasPose.y * canvasPose.z;
    return x >= -padding - 200 && x <= width + padding + 200 && y >= -padding - 30 && y <= height + padding + 30;
}

function paintBase () {
    clearSurface(ctx);
    paintEdges(ctx, drawing.batches);
    for (const dot of drawing.dots.values()) {
        ctx.fillStyle = dot.fill;
        ctx.fill(dot.path);
    }
    if (view.kind !== 'global' || canvasPose.z > 0.6) {
        ctx.font = '11px system-ui';
        ctx.fillStyle = '#b6c1d3';
        for (const node of drawing.nodes) if (inViewport(node)) ctx.fillText(node.label, node.x + 10, node.y + 4);
    }
}

function paintHighlights () {
    clearSurface(overlayCtx);
    const activeIds = [...new Set([selected, hover].filter(Boolean))];
    const activeEdges = new Set(), batches = new Map();
    for (const id of activeIds) for (const item of drawing.adjacency.get(id) || []) activeEdges.add(item);
    for (const item of activeEdges) edgeBatch(batches, item, true);
    paintEdges(overlayCtx, batches);
    for (const id of activeIds) {
        const node = drawing.points.get(id);
        if (!node || !inViewport(node)) continue;
        // A small static halo avoids an expensive shadow blur on every edge.
        overlayCtx.fillStyle = node.fill;
        overlayCtx.globalAlpha = 0.12;
        overlayCtx.beginPath();
        overlayCtx.arc(node.x, node.y, 13, 0, Math.PI * 2);
        overlayCtx.fill();
        overlayCtx.globalAlpha = 1;
        overlayCtx.beginPath();
        overlayCtx.arc(node.x, node.y, 8, 0, Math.PI * 2);
        overlayCtx.fill();
        overlayCtx.strokeStyle = '#e6eeff';
        overlayCtx.lineWidth = 1.5;
        overlayCtx.stroke();
        overlayCtx.font = 'bold 11px system-ui';
        // Opaque label backing prevents normal/bold glyphs from overlapping
        // across the independent base and selection layers.
        const labelWidth = overlayCtx.measureText(node.label).width + 12;
        overlayCtx.fillStyle = '#101927';
        overlayCtx.beginPath();
        overlayCtx.roundRect(node.x + 8, node.y - 10, labelWidth, 19, 4);
        overlayCtx.fill();
        overlayCtx.fillStyle = '#fff';
        overlayCtx.fillText(node.label, node.x + 10, node.y + 4);
    }
}

function draw () {
    const stamp = [graphRevision, width, height, pixelRatio,
                   functionFilter.checked, variableFilter.checked].join(':');
    const projection = cameraProjection();
    // During a gesture, transform the complete buffered scene on the compositor.
    // Refresh when new space is revealed, sharpness needs it, or input becomes idle.
    const outside = !canvasPose || projection.x > padding || projection.y > padding ||
                    projection.x + (width + 2 * padding) * projection.scale < width + padding ||
                    projection.y + (height + 2 * padding) * projection.scale < height + padding;
    const labelsChanged = canvasPose && view.kind === 'global' && (canvasPose.z > 0.6) !== (camera.z > 0.6);
    if (forceRefresh || !canvasPose || baseStamp !== stamp || outside || labelsChanged ||
        projection.scale < 0.66 || projection.scale > 1.55) {
        if (!drawing || drawing.revision !== graphRevision || drawing.zoom !== camera.z ||
            drawing.showFunctions !== functionFilter.checked || drawing.showVariables !== variableFilter.checked) prepareDrawing();
        canvasPose = {...camera};
        paintBase();
        baseStamp = stamp;
        projectedStamp = overlayStamp = '';
        forceRefresh = false;
        clearTimeout(refreshTimer);
        refreshTimer = 0;
    }
    const cameraStamp = [camera.x, camera.y, camera.z].join(':');
    if (projectedStamp !== cameraStamp) {
        const value = cameraProjection();
        const transform = `matrix(${value.scale},0,0,${value.scale},${value.x},${value.y})`;
        canvas.style.transform = overlay.style.transform = transform;
        projectedStamp = cameraStamp;
        if (canvasPose.x !== camera.x || canvasPose.y !== camera.y || canvasPose.z !== camera.z) {
            clearTimeout(refreshTimer);
            refreshTimer = setTimeout(() => {refreshTimer = 0; forceRefresh = true; requestDraw();}, 100);
        }
    }
    const highlightStamp = stamp + ':' + selected + ':' + hover;
    if (overlayStamp !== highlightStamp) {
        paintHighlights();
        overlayStamp = highlightStamp;
    }
}

function cameraProjection () {
    if (!canvasPose) return {scale: 1, x: 0, y: 0};
    const scale = camera.z / canvasPose.z;
    return {scale,
            x: (width / 2 + padding) * (1 - scale) + (canvasPose.x - camera.x) * camera.z,
            y: (height / 2 + padding) * (1 - scale) + (canvasPose.y - camera.y) * camera.z};
}

function updateHover () {
    const sample = pointerSample;
    pointerSample = null;
    const x = sample.x - canvasBox.left, y = sample.y - canvasBox.top;
    const next = pick(x, y), tooltip = $('tooltip');
    if (next !== hover) {
        hover = next;
        tooltip.style.display = hover ? 'block' : 'none';
        if (hover) tooltip.textContent = name(hover);
    }
    if (hover) tooltip.style.transform = `translate(${Math.max(0, Math.min(x + 16, width - 260))}px,${y + 18}px)`;
}

function fit(){if(!nodes.length)return;const xs=nodes.map(n=>n.x),ys=nodes.map(n=>n.y),loX=Math.min(...xs),hiX=Math.max(...xs),loY=Math.min(...ys),hiY=Math.max(...ys);camera={x:(loX+hiX)/2,y:(loY+hiY)/2,z:Math.min(1.4,(width-160)/Math.max(200,hiX-loX),(height-180)/Math.max(200,hiY-loY))};requestDraw()}
function setGraph(ids,rels,depths){nodeMap=new Map();nodes=ids.map((id,i)=>{const center=id===view.id,level=depths?.get(id)||1,angle=i*2.399963229728653,r=center?0:view.kind==='global'?Math.sqrt(i)*46:130+Math.sqrt(i)*42+(level-1)*70;const n={id,x:Math.cos(angle)*r,y:Math.sin(angle)*r};nodeMap.set(id,n);return n});edges=rels;assignEdgeLanes(edges);graphRevision++;hover=null;pointerSample=null;$('tooltip').style.display='none';fit()}

let globalGraphCache = null;
function globalGraph () {
    if (!globalGraphCache) {
        const ids = Object.keys(KB.modules).sort((a, b) => name(a).localeCompare(name(b))), groups = new Map();
        for (const [rid, relation] of Object.entries(KB.relations)) {
            const source = moduleOf(relation.source) || relation.source_module_id, target = moduleOf(relation.target);
            if (!source || !target || source === target) continue;
            const key = source + ':' + target;
            if (!groups.has(key)) groups.set(key, {source, target, rids: []});
            groups.get(key).rids.push(rid);
        }
        globalGraphCache = {ids, relations: [...groups.values()]};
    }
    setGraph(globalGraphCache.ids, globalGraphCache.relations);
    $('mode').textContent = `GLOBAL · ${globalGraphCache.ids.length} objects · ${globalGraphCache.relations.length} module connections`;
    showWelcome();
}

function neighbourhood(root,maxDepth){const queue=[[root,0]],depths=new Map([[root,0]]),picked=new Set();let clipped=false;for(let q=0;q<queue.length;q++){const [id,d]=queue[q];if(d>=maxDepth)continue;const dir=$('direction').value,items=[...(dir!=='incoming'?(outgoing.get(id)||[]):[]),...(dir!=='outgoing'?(incoming.get(id)||[]):[])];for(const rid of items){const r=KB.relations[rid];if(!r.source||!r.target||!entities[r.source]||!entities[r.target])continue;for(const other of [r.source,r.target]){if(!depths.has(other)){if(depths.size>=150){clipped=true;continue}depths.set(other,d+1);queue.push([other,d+1])}}if(depths.has(r.source)&&depths.has(r.target))picked.add(rid)}}return {depths,rels:[...picked].map(id=>({id,...KB.relations[id]})),clipped}}
function renderView(){if(view.kind==='global'){globalGraph();return}if(view.kind==='module'){const all=(owned.get(view.id)||[]).filter(s=>['function','variable'].includes(kind(s))),ids=new Set([view.id,...all.slice(0,75)]),rels=[];for(const rid of moduleRelations.get(view.id)||[]){const r=KB.relations[rid];if(!(ids.has(r.source)||ids.has(r.target)))continue;if(r.source&&r.target&&entities[r.source]&&entities[r.target]&&ids.size<150){ids.add(r.source);ids.add(r.target);rels.push({id:rid,...r})}}const ownership=all.filter(id=>ids.has(id)).map(id=>({source:view.id,target:id,ownership:true}));setGraph([...ids],[...ownership,...rels]);$('mode').textContent=`MODULE · ${name(view.id)} · ${all.length} owned symbols · up to 150 nodes shown`;showEntity(view.id)}else{const result=neighbourhood(view.id,Number($('depth').value));setGraph([...result.depths.keys()],result.rels,result.depths);$('mode').textContent=`SYMBOL · ${name(view.id)} · depth ${$('depth').value}${result.clipped?' · 150-node limit reached':''}`;showEntity(view.id)}selected=view.id;requestDraw()}
function navigate(next,record=true){view={...next};if(record){history=history.slice(0,historyIndex+1);history.push({...view});historyIndex=history.length-1}renderView();$('back').disabled=historyIndex<=0;$('forward').disabled=historyIndex>=history.length-1}
function showWelcome(){$('detail').innerHTML='<h2>Explore the project</h2><p class="muted">Find a module, function or variable. Double-click a node to explore its relationships. Select a source location to open that line.</p><div class="notice">Edges represent recorded build references. Function references are not confirmed calls; read/write access is not classified.</div><h3>Coverage</h3><p class="muted">'+Object.keys(KB.modules).length+' objects · '+Object.keys(KB.symbols).length+' symbols in this view · '+Object.keys(KB.relations).length+' reference groups.</p>'}
function relationEntry(rid,focus){const r=KB.relations[rid],other=(r.source===focus||KB.modules[focus]&&moduleOf(r.source)===focus)?r.target:r.source;let sites=r.sites||[];return `<div class="entry"><button class="name" data-entity="${esc(other||'')}">${esc(other?name(other):r.target_raw_name)}</button><div class="muted">${(r.source===focus||KB.modules[focus]&&moduleOf(r.source)===focus)?'References':'Referenced by'} · ${esc(KB.modules[moduleOf(other)]?.name||'')} · ${sites.length} sites</div><span class="tag">${esc(r.resolution)}</span>${sites.slice(0,2).map(s=>locationButtons(s.source_location)).join('')}<button class="loc" data-relation="${rid}">Details</button></div>`}

function bindRelations () {
    if (relationBinding) return;
    relationBinding = true;
    document.addEventListener('click', event => {
        const button = event.target.closest('[data-relation]');
        if (button) showRelation(button.dataset.relation);
    });
}

function showEntity(id){selected=id;const e=entities[id];if(!e)return;const isModule=!!KB.modules[id],ins=isModule?(moduleRelations.get(id)||[]).filter(r=>moduleOf(KB.relations[r].target)===id):(incoming.get(id)||[]),outs=isModule?(moduleRelations.get(id)||[]).filter(r=>(moduleOf(KB.relations[r].source)||KB.relations[r].source_module_id)===id):(outgoing.get(id)||[]);let body=`<h2>${esc(name(id))}</h2><div class="muted">${esc(isModule?'module':e.kind)} · ${esc(KB.modules[moduleOf(id)]?.name||'')}</div><div class="mono muted" style="margin-top:8px">${esc(id)}</div>`;if(isModule){body+='<h3>Source</h3>'+e.source_file_ids.map(fid=>locationButtons({file_id:fid,line:1})).join('');const children=(owned.get(id)||[]).filter(s=>['function','variable'].includes(kind(s)));body+=`<h3>Owned functions / variables (${children.length})</h3><div id="ownedList"></div><button id="moreOwned">Show more</button>`}else{body+='<h3>'+(e.definition?'Definition':'Declaration location')+'</h3>'+locationButtons(sourceLoc(id));if(e.address)body+=`<p class="mono">Address: ${esc(e.address)}</p>`;if(e.parent_function_id)body+=`<h3>Containing function</h3><button class="name action" data-entity="${esc(e.parent_function_id)}">${esc(name(e.parent_function_id))}</button>`;body+=`<div style="margin-top:10px"><button data-entity="${id}">Explore symbol</button> <button data-entity="${esc(e.module_id||'')}">Owner module</button></div>`}
body+=`<h3>Outgoing references (${outs.length})</h3>${outs.slice(0,15).map(r=>relationEntry(r,id)).join('')||'<p class="muted">None in the recorded data.</p>'}<h3>Incoming references (${ins.length})</h3>${ins.slice(0,15).map(r=>relationEntry(r,id)).join('')||'<p class="muted">None in the recorded data.</p>'}`;if(ins.length>15||outs.length>15)body+='<p class="muted">This panel shows 15 relations per direction. Use the graph or retriever to explore more.</p>';$('detail').innerHTML=body;animateDetail();if(isModule){const children=(owned.get(id)||[]).filter(s=>['function','variable'].includes(kind(s)));let shown=0;const more=()=>{const list=$('ownedList');list.insertAdjacentHTML('beforeend',children.slice(shown,shown+30).map(s=>`<div class="entry"><button class="name" data-entity="${s}">${esc(name(s))}</button><div class="muted">${esc(kind(s))}</div>${locationButtons(sourceLoc(s))}</div>`).join(''));shown+=30;$('moreOwned').style.display=shown>=children.length?'none':'inline-block';connectEntityButtons();bindLocations()};$('moreOwned').onclick=more;more()}connectEntityButtons();bindLocations();bindRelations();requestDraw()}
function showRelation(rid){const r=KB.relations[rid];$('detail').innerHTML=`<h2>${esc(relationLabel(r))}</h2><div class="entry"><button class="name" data-entity="${esc(r.source)}">${esc(name(r.source))}</button><p>↓ references</p><button class="name" data-entity="${esc(r.target)}">${esc(r.target?name(r.target):r.target_raw_name+' (not uniquely resolved)')}</button></div><span class="tag">${esc(r.resolution)}</span><p class="muted">Read/write access and direct/indirect calls are not classified.</p><h3>All reference sites (${r.sites.length})</h3>`+r.sites.map(s=>`<div class="entry"><div class="mono">${esc(s.reference_address||'No address available')}</div>${locationButtons(s.source_location)}<div class="muted">access: ${esc(s.access)} · dispatch: ${esc(s.dispatch)}</div></div>`).join('');if((r.target_candidates||[]).length>1){$('detail').insertAdjacentHTML('beforeend','<h3>Target candidates</h3>'+r.target_candidates.map(id=>`<div class="entry"><button class="name" data-entity="${id}">${esc(name(id))}</button></div>`).join(''))}animateDetail();connectEntityButtons();bindLocations()}
function mappedPath(fid){const f=KB.files[fid],root=$('workspace').value.trim().replace(/\\/g,'/').replace(/\/$/,'');return root&&f.project_relative_path?root+'/'+f.project_relative_path:f.recorded_path}
function editorUri(fid,line,column){const path=mappedPath(fid);if(!path||!line||!/^([A-Za-z]:\/|\/)/.test(path))return null;return 'vscode://file/'+path.replace(/^\/+/,'').split('/').map(s=>encodeURIComponent(s).replace(/%3A/gi,':')).join('/')+':'+line+':'+(column||1)}
const normalized=s=>String(s||'').replace(/\\/g,'/').replace(/^\.\//,'').toLowerCase();
const localFileMatches = new Map();
function findLocalFile(fid){const f=KB.files[fid],relative=normalized(f.project_relative_path),recorded=normalized(f.recorded_path);const exact=localFiles.filter(x=>relative&&x.relative===relative);if(exact.length===1)return exact[0].file;if(exact.length>1)return null;const suffix=localFiles.filter(x=>x.relative.includes('/')&&(recorded.endsWith('/'+x.relative)||relative.endsWith('/'+x.relative)));return suffix.length===1?suffix[0].file:null}

function matchLocalFile (fid) {
    if (!localFileMatches.has(fid)) localFileMatches.set(fid, findLocalFile(fid));
    return localFileMatches.get(fid);
}
async function showSource(fid,line,column=1){pendingSource={fid,line,column};$('sourcePane').style.display='block';$('sourceTitle').textContent=(mappedPath(fid)||KB.files[fid]?.name||fid)+':'+line;const uri=editorUri(fid,line,column);$('editor').style.display=uri?'inline-block':'none';if(uri)$('editor').href=uri;const file=matchLocalFile(fid);if(!file){$('sourceBody').innerHTML='<div style="padding:18px;white-space:normal;font:13px/1.6 system-ui">Select your project root under <b>Connect local source</b> to view this line in the graph. You can also open it with <b>VS Code</b>.<p class="muted">Files are not matched by basename alone. If your workspace has moved, enter its current path.</p></div>';$('sourceSettings').open=true;return}if(file.size>20*1024*1024){$('sourceBody').textContent='This file exceeds 20 MiB; open it in your editor.';return}const text=await file.text();if(pendingSource?.fid!==fid||pendingSource?.line!==line)return;const lines=text.replace(/^\uFEFF/,'').split(/\r?\n/);if(line<1||line>lines.length){$('sourceBody').textContent=`This file has ${lines.length} lines; the build points to line ${line}. Check the source revision.`;return}const low=Math.max(1,line-60),high=Math.min(lines.length,line+100);$('sourceBody').innerHTML=lines.slice(low-1,high).map((t,i)=>`<div class="sourceLine ${low+i===line?'target':''}" data-source-line="${low+i}"><span class="number">${low+i}</span><span>${esc(t)}</span></div>`).join('');$('sourceBody').querySelector('.target')?.scrollIntoView({block:'center'});}
$('chooseFolder').onclick=()=>$('folder').click();$('folder').onchange=()=>{localFileMatches.clear();localFiles=Array.from($('folder').files).filter(f=>/\.(c|h|cc|cpp|hpp|s|asm|inc|ld|lsl|arxml)$/i.test(f.name)).map(file=>({file,relative:normalized(file.webkitRelativePath.split('/').slice(1).join('/'))}));$('sourceStatus').textContent=`Selected ${localFiles.length} source files. Files are read locally when a location is opened.`;if(pendingSource)showSource(pendingSource.fid,pendingSource.line,pendingSource.column)};
$('workspace').value=KB.source_root||'';$('workspace').onchange=()=>{if(pendingSource)showSource(pendingSource.fid,pendingSource.line,pendingSource.column)};$('closeSource').onclick=()=>{$('sourcePane').style.display='none';pendingSource=null};
function pick(x,y){let best=null,dist=15;for(const n of nodes){if(!visible(n.id))continue;const p=screen(n),d=Math.hypot(p.x-x,p.y-y);if(d<dist){dist=d;best=n.id}}return best}
function pickEdge (x, y) {
    let picked = null, nearest = 6;
    for (const edge of edges) {
        if (!edge.id || !visible(edge.source) || !visible(edge.target)) continue;
        const geometry = edgeGeometry(edge);
        if (!geometry) continue;

        const controls = geometry.kind === 'cubic' ? [geometry.p, geometry.c1, geometry.c2, geometry.q] : [geometry.p, geometry.c1, geometry.q];
        const minX = Math.min(...controls.map(point => point.x)), maxX = Math.max(...controls.map(point => point.x));
        const minY = Math.min(...controls.map(point => point.y)), maxY = Math.max(...controls.map(point => point.y));
        if (x < minX - nearest || x > maxX + nearest || y < minY - nearest || y > maxY + nearest) continue;
        const distance = curveDistance(geometry, x, y);
        if (distance < nearest) {
            nearest = distance;
            picked = edge.id;
        }
    }
    return picked;
}
canvas.onpointerdown= event => {
    drag = {x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false};
    canvas.setPointerCapture(event.pointerId);
};
canvas.onpointermove = event => {
    if (drag) {
        const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
        drag.moved = drag.moved || Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) > 2;
        camera.x -= dx / camera.z;
        camera.y -= dy / camera.z;
        drag.x = event.clientX;
        drag.y = event.clientY;
        pointerSample = null;
        if (hover) {hover = null; $('tooltip').style.display = 'none';}
    } else {
        pointerSample = {x: event.clientX, y: event.clientY};
    }
    requestDraw();
};
canvas.onpointerup = event => {
    if (drag && !drag.moved) {
        const x = event.clientX - canvasBox.left, y = event.clientY - canvasBox.top;
        const id = pick(x, y);
        if (id) showEntity(id);
        else {const rid = pickEdge(x, y); if (rid) showRelation(rid);}
    }
    drag = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
};
canvas.onpointercancel = canvas.onlostpointercapture = () => {drag = null;};
canvas.onpointerleave = () => {
    pointerSample = null;
    if (!drag && hover) {hover = null; $('tooltip').style.display = 'none'; requestDraw();}
};
canvas.ondblclick = event => {
    const id = pick(event.clientX - canvasBox.left, event.clientY - canvasBox.top);
    if (id) navigate({kind: KB.modules[id] ? 'module' : 'symbol', id});
};
canvas.addEventListener('wheel', event => {
    event.preventDefault();
    const x = event.clientX - canvasBox.left - width / 2;
    const y = event.clientY - canvasBox.top - height / 2, old = camera.z;
    camera.z = Math.max(0.08, Math.min(5, old * Math.exp(-event.deltaY * 0.001)));
    camera.x += x / old - x / camera.z;
    camera.y += y / old - y / camera.z;
    requestDraw();
}, {passive: false});

// Preserve the original exact-first, stable ordering and the 60-result cap.
const searchable = Object.keys(entities).filter(id => name(id) && !KB.symbols[id]?.is_declaration);
const searchNames = searchable.map(id => name(id).toLowerCase());
let searchFrame = 0;
function searchMatches (query) {
    const exact = [], partial = [];
    for (let index = 0; index < searchable.length; index++) {
        const id = searchable[index], label = searchNames[index];
        if (label === query) {
            exact.push(id);
            if (exact.length === 60) break;
        } else if (partial.length < 60 && (label.includes(query) || id === query)) partial.push(id);
    }
    return exact.concat(partial).slice(0, 60);
}
function renderSearch () {
    searchFrame = 0;
    const query = $('search').value.trim().toLowerCase();
    if (!query) {$('results').style.display = 'none'; return;}
    $('results').innerHTML = searchMatches(query).map(id =>
        `<button class="result" data-entity="${id}">${esc(name(id))}<small>${esc(kind(id))} · ${esc(KB.modules[moduleOf(id)]?.name || '')} · ${esc(labelLocation(sourceLoc(id)))}</small></button>`
    ).join('') || '<div class="entry">No matches found.</div>';
    $('results').style.display = 'block';
}
function closeSearch () {
    if (searchFrame) cancelAnimationFrame(searchFrame);
    searchFrame = 0;
    $('results').style.display = 'none';
}
$('search').oninput = () => {if (!searchFrame) searchFrame = requestAnimationFrame(renderSearch);};
$('search').onkeydown = event => {
    if (event.key === 'Escape') closeSearch();
    if (event.key === 'Enter') {
        if (searchFrame) cancelAnimationFrame(searchFrame);
        renderSearch();
        $('results').querySelector('button')?.click();
    }
};
document.addEventListener('click', event => {if (!event.target.closest('#searchBox')) closeSearch();});

$('back').onclick=()=>{if(historyIndex>0){historyIndex--;navigate(history[historyIndex],false)}};$('forward').onclick=()=>{if(historyIndex<history.length-1){historyIndex++;navigate(history[historyIndex],false)}};$('global').onclick=()=>navigate({kind:'global'});$('fit').onclick=fit;for(const id of ['depth','direction'])$(id).onchange=renderView;for(const id of ['functions','variables'])$(id).onchange=requestDraw;window.addEventListener('resize',resize);$('counts').textContent=`${Object.keys(KB.modules).length.toLocaleString()} objects · ${Object.keys(KB.symbols).length.toLocaleString()} symbols · ${Object.keys(KB.relations).length.toLocaleString()} references`;
bindLocations();connectEntityButtons();bindRelations();
resize();navigate({kind:'global'});
</script></body></html>'''


def resolve_input_path (input_path, legacy_input):
    if input_path and legacy_input:
        raise ValueError(
            "Use either --input or positional knowledge_base, not both."
        )

    selected = (
        input_path
        if input_path
        else legacy_input
    )

    path = (
        Path(selected).expanduser().resolve()
        if selected
        else Path(DEFAULT_KB).expanduser().resolve()
    )

    if path.suffix.casefold() != ".json":
        raise ValueError(
            f"Input file must use .json extension: {path}"
        )

    if not path.is_file():
        raise ValueError(
            f"Input knowledge-base file does not exist: {path}"
        )

    return path


def resolve_output_path (output_path):
    if output_path is None:
        return DEFAULT_HTML.resolve()

    path = Path(output_path).expanduser()

    if not path.suffix: path = path.with_suffix(".html")

    elif path.suffix.casefold() != ".html":
        raise ValueError(f"Output file must use .html extension: {path}")

    path = path.resolve()
    path.parent.mkdir( parents=True, exist_ok=True )

    return path

def main ():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "legacy_knowledge_base",
        nargs="?",
        help=argparse.SUPPRESS
    )

    parser.add_argument(
        "-i",
        "--input",
        "--inputs",
        dest="knowledge_base",
        metavar="KNOWLEDGE_BASE_JSON",
        help=(
            "Input knowledge-base JSON file; "
            "defaults to knowledge_base.json beside the script"
        )
    )

    parser.add_argument(
        "-o",
        "--output",
        metavar="OUTPUT_HTML",
        help=(
            "Output HTML file path/name; "
            "defaults to knowledge_graph.html beside the script"
        )
    )

    parser.add_argument(
        "--source-root",
        default="",
        help=(
            "Local project root used for VS Code links; "
            "editable in the HTML"
        )
    )

    args = parser.parse_args()

    try:
        input_path = resolve_input_path(args.knowledge_base,args.legacy_knowledge_base)

        output_path = resolve_output_path(args.output)
        kb = KnowledgeBase(input_path)
        data = visual_data( kb, args.source_root )
        payload = json.dumps(   data,
                                ensure_ascii=False,
                                separators=(",", ":")
                            ).replace("<","\\u003c").replace("\u2028","\\u2028").replace("\u2029","\\u2029")

        temporary = output_path.with_name(output_path.name + ".tmp")

        try:
            temporary.write_text( HTML.replace( "__KNOWLEDGE_DATA__", payload ), encoding="utf-8" )
            temporary.replace(output_path)

        finally: temporary.unlink( missing_ok=True )

        print(
            f"[OK] {output_path} | "
            f"{len(data['symbols']):,} symbols | "
            f"{len(data['relations']):,} reference groups"
        )

    except ( OSError, ValueError, KeyError ) as error:
        parser.exit( 1, f"[ERROR] {error}\n" )


if __name__ == "__main__":
    main()
