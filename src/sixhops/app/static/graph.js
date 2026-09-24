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

  const style = () => [
    { selector: "node", style: {
      label: "data(name)", "font-size": 11, color: css("--text"), "text-valign": "bottom",
      "text-margin-y": 4, "background-color": css("--person"), width: 22, height: 22,
      "text-outline-color": css("--bg"), "text-outline-width": 2,
    } },
    { selector: "node[kind = 'me']", style: {
      shape: "star", width: 44, height: 44, "background-color": css("--me"),
      "font-size": 13, "font-weight": "bold",
    } },
    { selector: "node[kind = 'company']", style: {
      shape: "round-rectangle", width: 28, height: 22, "background-color": css("--company"),
    } },
    { selector: "node[kind = 'school']", style: {
      shape: "hexagon", width: 28, height: 24, "background-color": css("--school"),
    } },
    { selector: "edge", style: {
      width: "mapData(strength, 1, 5, 1, 5)", "line-color": css("--border"),
      "curve-style": "bezier", opacity: 0.9,
    } },
    { selector: "edge[kind = 'WORKED_AT']", style: { "line-style": "dashed" } },
    { selector: "edge[kind = 'STUDIED_AT']", style: { "line-style": "dotted" } },
    { selector: ":selected", style: {
      "border-width": 3, "border-color": css("--accent"), "line-color": css("--accent"),
    } },
    { selector: ".faded", style: { opacity: 0.15 } },
    { selector: ".on-path", style: {
      "line-color": css("--accent"), "border-width": 3, "border-color": css("--accent"), opacity: 1,
    } },
  ];

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
    if (unplaced.length === cy.nodes().length) {
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

  function layout() {
    const l = cy.layout({ name: "cose", animate: false, randomize: true, nodeRepulsion: 20000,
      idealEdgeLength: 100, nodeOverlap: 20, padding: 30 });
    l.on("layoutstop", savePositions);
    l.run();
  }

  function select(id) {
    const target = cy.getElementById(id);
    cy.elements().unselect();
    if (target.empty()) return;
    target.select();
    if (target.isNode()) cy.animate({ center: { eles: target }, duration: 250 });
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
    cy.animate({ fit: { eles: onPath, padding: 80 }, duration: 300 });
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
    highlightPath(panel.querySelector(".path"));  // first (best) path, or clear
  });

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
  document.getElementById("fit").addEventListener("click", () => cy.fit(undefined, 30));
  document.getElementById("relayout").addEventListener("click", () => layout());
  window.matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => cy.style(style()));

  refresh();
})();
