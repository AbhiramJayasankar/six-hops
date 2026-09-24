// Network view: renders /api/graph with Cytoscape and keeps it in sync with the side panel.
// All edits happen server-side via HTMX; responses fire "graph-changed" and we re-fetch.
(() => {
  const el = document.getElementById("cy");
  const panel = document.getElementById("panel");
  const POS_KEY = "sixhops.positions";

  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const loadPositions = () => {
    try { return JSON.parse(localStorage.getItem(POS_KEY)) || {}; } catch { return {}; }
  };
  const savePositions = () => {
    const pos = {};
    cy.nodes().forEach((n) => { pos[n.id()] = n.position(); });
    try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch { /* storage unavailable */ }
  };

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const narrow = window.matchMedia("(max-width: 800px)");
  const font = '"Overpass", system-ui, sans-serif';

  // Stations on a transit map: people are rings, companies squares, schools diamonds-ish
  // hexagons, and Me is the red "you are here" marker. Colours come from the CSS tokens.
  const style = () => [
    { selector: "node", style: {
      label: "data(name)", "font-family": font, "font-size": 11, "font-weight": 600,
      "min-zoomed-font-size": 7,  // hide labels when zoomed far out (large imports)
      color: css("--ink"), "text-valign": "bottom", "text-margin-y": 5,
      "text-outline-color": css("--paper"), "text-outline-width": 2.5,
      width: 16, height: 16, "background-color": css("--surface"),
      "border-width": 3.5, "border-color": css("--person"),
    } },
    { selector: "node[kind = 'me']", style: {
      width: 26, height: 26, "background-color": css("--here"), "border-color": css("--surface"),
      "border-width": 3, "outline-width": 3, "outline-color": css("--here"), "outline-offset": 0,
      "font-size": 13, "font-weight": 800,
    } },
    { selector: "node[kind = 'company']", style: {
      shape: "round-rectangle", width: 20, height: 20, "background-color": css("--company"),
      "border-width": 0,
    } },
    { selector: "node[kind = 'school']", style: {
      shape: "hexagon", width: 22, height: 20, "background-color": css("--school"), "border-width": 0,
    } },
    { selector: "edge", style: {
      width: "mapData(strength, 1, 5, 1, 4)", "line-color": css("--rule"), "curve-style": "bezier",
    } },
    { selector: "edge[kind = 'WORKED_AT']", style: { "line-style": "dashed", "line-dash-pattern": [6, 4] } },
    { selector: "edge[kind = 'STUDIED_AT']", style: { "line-style": "dotted" } },
    { selector: "node:selected", style: { "underlay-color": css("--route"), "underlay-opacity": 0.25,
      "underlay-padding": 6, "underlay-shape": "ellipse" } },
    { selector: "edge:selected", style: { "line-color": css("--route") } },
    { selector: ".faded", style: { opacity: 0.18 } },
    { selector: "edge.on-path", style: {
      "line-color": css("--route"), width: "mapData(strength, 1, 5, 2.5, 7)", opacity: 1, "z-index": 10,
    } },
    { selector: "node.on-path", style: { opacity: 1, "z-index": 10, "font-size": 12, "font-weight": 750 } },
  ];

  const animate = (opts) => {
    if (reducedMotion.matches) cy.stop().fit(opts.fit ? opts.fit.eles : undefined, opts.fit ? opts.fit.padding : 30);
    else cy.stop().animate({ ...opts, duration: 280 });
  };

  const cy = cytoscape({ container: el, style: style(), wheelSensitivity: 0.3, maxZoom: 3, minZoom: 0.1 });
  window.sixhops = { cy };

  async function refresh(selectId) {
    const resp = await fetch(el.dataset.graphUrl, { headers: { Accept: "application/json" } });
    if (!resp.ok) return;
    const g = await resp.json();
    const saved = loadPositions();
    const wanted = new Set([...Object.keys(g.nodes), ...Object.keys(g.edges)]);
    cy.elements().filter((e) => !wanted.has(e.id())).remove();

    const added = [];
    for (const n of Object.values(g.nodes)) {
      const data = { id: n.id, name: n.name, kind: n.kind };
      const existing = cy.getElementById(n.id);
      if (existing.nonempty()) { existing.data(data); continue; }
      added.push(cy.add({ group: "nodes", data, position: saved[n.id] || { x: 0, y: 0 } }));
    }
    for (const e of Object.values(g.edges)) {
      const data = { id: e.id, source: e.src, target: e.dst, kind: e.kind, strength: e.strength };
      const existing = cy.getElementById(e.id);
      if (existing.nonempty()) existing.data(data); else cy.add({ group: "edges", data });
    }

    const unplaced = added.filter((n) => !saved[n.id()]);
    // Lay everything out again when the graph is new or grew a lot at once (an import);
    // otherwise place the few new nodes next to a neighbour so the view doesn't jump.
    if (unplaced.length === cy.nodes().length || unplaced.length > 25) {
      layout();
    } else if (unplaced.length) {
      // Place new nodes next to a neighbour instead of re-running the layout, so the view
      // doesn't jump after every edit. "Re-layout" tidies up on demand.
      for (const n of unplaced) {
        const anchor = n.neighborhood("node").filter((m) => !unplaced.includes(m)).first();
        const p = anchor.nonempty() ? anchor.position() : centre();
        const angle = Math.random() * 2 * Math.PI;
        n.position({ x: p.x + 90 * Math.cos(angle), y: p.y + 90 * Math.sin(angle) });
      }
      savePositions();
    }
    fillFinder(g);
    if (selectId) select(selectId);
  }

  function centre() {
    const e = cy.extent();
    return { x: (e.x1 + e.x2) / 2, y: (e.y1 + e.y2) / 2 };
  }

  // Force-directed layout reads best for small graphs, but takes most of a minute on a
  // LinkedIn-sized one. Above LARGE nodes, draw rings by distance from Me instead: you in the
  // middle, the people you know around you, then the next hop, and so on.
  const LARGE = 250;

  function layout() {
    const me = cy.nodes("[kind = 'me']");
    let options = { name: "cose", animate: false, randomize: true, nodeRepulsion: 20000,
      idealEdgeLength: 100, nodeOverlap: 20, padding: 30 };
    if (cy.nodes().length > LARGE && me.nonempty()) {
      // Ring value: hop distance first, then split crowded distances into rings of ~100.
      const depth = {};
      cy.elements().breadthFirstSearch({ root: me, visit: (v, e, u, i, d) => { depth[v.id()] = d; } });
      const seen = {};
      const ring = {};
      cy.nodes().sort((a, b) => a.data("name").localeCompare(b.data("name"))).forEach((n) => {
        const d = depth[n.id()] ?? 9;
        seen[d] = (seen[d] || 0) + 1;
        ring[n.id()] = (10 - d) * 1000 - Math.floor((seen[d] - 1) / 100);
      });
      options = { name: "concentric", animate: false, padding: 30, minNodeSpacing: 6,
        concentric: (n) => ring[n.id()], levelWidth: () => 1 };
    }
    const l = cy.layout(options);
    l.on("layoutstop", savePositions);
    l.run();
  }

  function select(id) {
    const target = cy.getElementById(id);
    cy.elements().unselect();
    if (target.empty()) return;
    target.select();
    if (target.isNode()) {
      if (reducedMotion.matches) cy.center(target); else cy.animate({ center: { eles: target }, duration: 250 });
    }
  }

  function fillFinder(g) {
    const options = (nodes) => nodes
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((n) => Object.assign(document.createElement("option"), { value: `${n.name} (#${n.id})` }));
    const nodes = Object.values(g.nodes);
    document.getElementById("find-options").replaceChildren(...options(nodes));
    document.getElementById("company-options")
      .replaceChildren(...options(nodes.filter((n) => n.kind === "company")));
  }

  function highlightPath(item) {
    cy.elements().removeClass("on-path faded");
    panel.querySelectorAll(".path.active").forEach((p) => p.classList.remove("active"));
    if (!item) return;
    item.classList.add("active");
    const ids = [...item.dataset.pathNodes.split(","), ...item.dataset.pathEdges.split(",")];
    const onPath = cy.collection(ids.map((id) => cy.getElementById(id)).filter((e) => e.nonempty()));
    cy.elements().not(onPath).addClass("faded");
    onPath.addClass("on-path");
    fitTo(onPath);
  }

  function fitTo(eles) {
    // Size the padding to the canvas so a path never shrinks to a dot on a phone, and cap the
    // zoom so a two-stop path doesn't fill the screen.
    cy.resize();
    const padding = Math.round(Math.min(80, Math.max(24, Math.min(cy.width(), cy.height()) * 0.12)));
    const before = cy.maxZoom();
    cy.maxZoom(1.6);
    animate({ fit: { eles, padding } });
    cy.maxZoom(before);
  }

  const openPanel = (url) => htmx.ajax("GET", url, { target: panel, swap: "innerHTML" });

  cy.on("tap", "node", (evt) => openPanel(el.dataset.nodeUrl + evt.target.id()));
  cy.on("tap", "edge", (evt) => openPanel(el.dataset.edgeUrl + evt.target.id()));
  cy.on("tap", (evt) => {
    if (evt.target === cy) { cy.elements().unselect(); openPanel(panel.getAttribute("hx-get")); }
  });
  cy.on("dragfree", "node", savePositions);
  panel.addEventListener("htmx:afterSwap", () => {
    panel.scrollTop = 0;
    const best = panel.querySelector(".path");
    highlightPath(best);  // first (best) path, or clear
    // On phones the panel sits under the map: bring the map (with the highlighted path) into
    // view, with the top of the results just below it.
    if (best && narrow.matches) {
      el.closest(".canvas-wrap").scrollIntoView({ block: "start", behavior: reducedMotion.matches ? "auto" : "smooth" });
    }
  });
  // Keep Cytoscape's idea of the canvas size in sync with layout changes (toolbar wrapping,
  // rotating a phone, the panel appearing below the map).
  new ResizeObserver(() => cy.resize()).observe(el);

  document.body.addEventListener("graph-changed", (evt) => refresh(evt.detail && evt.detail.select));
  panel.addEventListener("click", (evt) => {
    const path = evt.target.closest(".path");
    if (path) { highlightPath(path); return; }
    const link = evt.target.closest("[data-select-node], [data-select-edge], [data-deselect]");
    if (!link) return;
    if (link.hasAttribute("data-deselect")) cy.elements().unselect();
    else select(link.dataset.selectNode || link.dataset.selectEdge);
  });

  panel.addEventListener("keydown", (evt) => {
    const path = evt.target.closest(".path");
    if (path && (evt.key === "Enter" || evt.key === " ")) { evt.preventDefault(); highlightPath(path); }
  });

  document.getElementById("find-node").addEventListener("change", (evt) => {
    const m = evt.target.value.match(/\(#(\w+)\)\s*$/);
    if (!m) return;
    select(m[1]);
    openPanel(el.dataset.nodeUrl + m[1]);
    evt.target.value = "";
  });
  document.getElementById("fit").addEventListener("click", () => { cy.resize(); cy.fit(undefined, 30); });
  document.getElementById("relayout").addEventListener("click", () => layout());
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => cy.style(style()));

  // Canvas labels need the web font loaded before first paint, or they stay in the fallback.
  document.fonts.ready.then(() => cy.style(style()));
  refresh();
})();
