const WorldView = (() => {
  const NODE_WIDTH = 240;
  const COLLAPSED_HEIGHT = 64;
  const EXPANDED_HEIGHT = 210;
  const H_GAP = 280;
  const V_GAP = 90;
  const PADDING = 80;
  const MIN_SCALE = 0.4;
  const MAX_SCALE = 1.6;
  const ZOOM_STEP = 1.12;

  let elements = {};
  let snapshot = null;
  let collapsedNodes = new Set();
  let openNodes = new Set();
  let collapseInitialized = false;
  let pendingSaves = new Map();
  let pan = { x: 0, y: 0 };
  let scale = 1;
  let sceneSize = { width: 0, height: 0 };
  let dragState = null;

  function init(config) {
    const root = document.getElementById(config.rootId);
    if (!root) {
      return;
    }

    root.innerHTML = `
      <div class="world-header">
        <div>
          <h2>世界设定</h2>
          <p class="hint">拖动画布浏览结构，节点可折叠，编辑自动保存。</p>
        </div>
        <div class="world-meta">
          <div class="save-path"></div>
          <div class="world-tools">
            <button class="world-tool" data-action="zoom-out" type="button">-</button>
            <button class="world-tool" data-action="zoom-in" type="button">+</button>
            <button class="world-tool" data-action="fit" type="button">适配</button>
          </div>
        </div>
      </div>
      <div class="world-status"></div>
      <div class="world-board">
        <div class="world-scene">
          <svg class="world-lines"></svg>
          <div class="world-canvas"></div>
        </div>
      </div>
    `;

    elements = {
      root,
      board: root.querySelector(".world-board"),
      scene: root.querySelector(".world-scene"),
      canvas: root.querySelector(".world-canvas"),
      lines: root.querySelector(".world-lines"),
      status: root.querySelector(".world-status"),
      savePath: root.querySelector(".save-path"),
    };

    if (!elements.board || !elements.scene || !elements.canvas || !elements.lines) {
      return;
    }

    elements.board.addEventListener("pointerdown", onPointerDown);
    elements.board.addEventListener("pointermove", onPointerMove);
    elements.board.addEventListener("pointerup", onPointerUp);
    elements.board.addEventListener("pointerleave", onPointerUp);

    root.querySelectorAll(".world-tool").forEach((button) => {
      button.addEventListener("click", () => {
        const action = button.dataset.action;
        if (action === "zoom-in") {
          zoomBy(ZOOM_STEP);
        } else if (action === "zoom-out") {
          zoomBy(1 / ZOOM_STEP);
        } else if (action === "fit") {
          fitToView();
        }
      });
    });

    setPan(0, 0);
  }

  async function load() {
    try {
      const response = await fetch("/api/world");
      const data = await response.json();
      if (!data.ok) {
        throw new Error("暂无世界数据。");
      }
      snapshot = data.snapshot;
      collapsedNodes.clear();
      openNodes.clear();
      collapseInitialized = false;
      pendingSaves.forEach((timer) => clearTimeout(timer));
      pendingSaves.clear();
      if (elements.savePath) {
        elements.savePath.textContent = data.save_path
          ? `存档：${data.save_path}`
          : "";
      }
      render();
      fitToView();
      setStatus("世界结构已加载，修改会自动保存。", false);
    } catch (error) {
      snapshot = null;
      if (elements.canvas) {
        elements.canvas.innerHTML = "";
      }
      if (elements.lines) {
        elements.lines.innerHTML = "";
      }
      if (elements.savePath) {
        elements.savePath.textContent = "";
      }
      setStatus(error.message, true);
    }
  }

  function setStatus(message, isError) {
    if (!elements.status) {
      return;
    }
    elements.status.textContent = message;
    elements.status.classList.toggle("is-error", Boolean(isError));
  }

  function buildTree(data, rootId, depth = 0) {
    const node = data[rootId];
    if (!node) {
      return null;
    }
    const children = (node.children || [])
      .map((childId) => buildTree(data, childId, depth + 1))
      .filter(Boolean);
    return {
      id: rootId,
      key: node.key || node.title || rootId,
      value: node.value || "",
      depth,
      children,
      childCount: children.length,
    };
  }

  function initializeCollapse(node) {
    if (!node) {
      return;
    }
    if (node.depth >= 1) {
      collapsedNodes.add(node.id);
    }
    node.children.forEach(initializeCollapse);
  }

  function applyCollapse(node) {
    if (collapsedNodes.has(node.id)) {
      node.children = [];
      return;
    }
    node.children.forEach(applyCollapse);
  }

  function assignHeights(node) {
    node.height = openNodes.has(node.id) ? EXPANDED_HEIGHT : COLLAPSED_HEIGHT;
    node.children.forEach(assignHeights);
  }

  function layoutTree(node, depth, yStart) {
    node.x = depth * H_GAP;
    if (!node.children.length) {
      node.y = yStart;
      return node.height;
    }

    let currentY = yStart;
    const childCenters = [];
    for (const child of node.children) {
      const childHeight = layoutTree(child, depth + 1, currentY);
      childCenters.push(child.y + child.height / 2);
      currentY += childHeight + V_GAP;
    }

    const totalHeight = currentY - yStart - V_GAP;
    const centerY =
      childCenters.length === 1
        ? childCenters[0]
        : (childCenters[0] + childCenters[childCenters.length - 1]) / 2;
    node.y = centerY - node.height / 2;
    return Math.max(totalHeight, node.height);
  }

  function collect(node, nodes, links) {
    nodes.push(node);
    node.children.forEach((child) => {
      links.push({ from: node, to: child });
      collect(child, nodes, links);
    });
  }

  function render() {
    if (!snapshot || !elements.canvas || !elements.scene || !elements.lines) {
      return;
    }
    const root = buildTree(snapshot, "world", 0);
    if (!root) {
      setStatus("世界根节点不存在。", true);
      return;
    }

    if (!collapseInitialized) {
      initializeCollapse(root);
      collapseInitialized = true;
    }
    applyCollapse(root);
    assignHeights(root);
    layoutTree(root, 0, 0);

    const nodes = [];
    const links = [];
    collect(root, nodes, links);

    const maxX = Math.max(...nodes.map((node) => node.x));
    const maxY = Math.max(...nodes.map((node) => node.y + node.height));
    const sceneWidth = maxX + NODE_WIDTH + PADDING * 2;
    const sceneHeight = maxY + PADDING * 2;
    sceneSize = { width: sceneWidth, height: sceneHeight };

    elements.scene.style.width = `${sceneWidth}px`;
    elements.scene.style.height = `${sceneHeight}px`;

    renderLines(links, sceneWidth, sceneHeight);
    renderNodes(nodes);
  }

  function renderLines(links, width, height) {
    if (!elements.lines) {
      return;
    }
    elements.lines.setAttribute("width", String(width));
    elements.lines.setAttribute("height", String(height));
    elements.lines.setAttribute("viewBox", `0 0 ${width} ${height}`);
    elements.lines.innerHTML =
      '<defs><linearGradient id="lineGlow" x1="0" x2="1" y1="0" y2="1">'
      + '<stop offset="0%" stop-color="#d86a4b" stop-opacity="0.6" />'
      + '<stop offset="100%" stop-color="#5c5147" stop-opacity="0.6" />'
      + '</linearGradient></defs>';

    for (const link of links) {
      const startX = link.from.x + PADDING + NODE_WIDTH;
      const startY = link.from.y + PADDING + link.from.height / 2;
      const endX = link.to.x + PADDING;
      const endY = link.to.y + PADDING + link.to.height / 2;
      const midX = startX + (endX - startX) * 0.5;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute(
        "d",
        `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`
      );
      path.setAttribute("stroke", "url(#lineGlow)");
      path.setAttribute("stroke-width", "2");
      path.setAttribute("fill", "none");
      path.setAttribute("stroke-linecap", "round");
      elements.lines.appendChild(path);
    }
  }

  function renderNodes(nodes) {
    if (!elements.canvas) {
      return;
    }
    elements.canvas.innerHTML = "";
    const fragment = document.createDocumentFragment();
    for (const node of nodes) {
      const card = document.createElement("div");
      card.className = "world-node";
      card.dataset.nodeId = node.id;
      card.dataset.depth = String(node.depth);
      card.classList.toggle("is-open", openNodes.has(node.id));
      card.style.transform = `translate(${node.x + PADDING}px, ${node.y + PADDING}px)`;
      card.style.minHeight = `${node.height}px`;

      const header = document.createElement("div");
      header.className = "node-head";

      const toggle = document.createElement("button");
      toggle.className = "node-toggle";
      toggle.type = "button";
      const hasChildren = node.childCount > 0;
      const isCollapsed = collapsedNodes.has(node.id);
      toggle.textContent = hasChildren ? (isCollapsed ? "+" : "-") : "o";
      toggle.title = hasChildren ? (isCollapsed ? "展开" : "折叠") : "无子节点";
      toggle.disabled = !hasChildren;
      if (hasChildren) {
        toggle.addEventListener("click", (event) => {
          event.stopPropagation();
          const wasCollapsed = collapsedNodes.has(node.id);
          if (collapsedNodes.has(node.id)) {
            collapsedNodes.delete(node.id);
          } else {
            collapsedNodes.add(node.id);
          }
          render();
          if (wasCollapsed) {
            fitToView();
          }
        });
      }

      const title = document.createElement("div");
      title.className = "node-title";
      title.textContent = node.key || node.id;

      header.appendChild(toggle);
      header.appendChild(title);
      card.appendChild(header);

      header.addEventListener("click", () => {
        const wasOpen = openNodes.has(node.id);
        if (wasOpen) {
          openNodes.delete(node.id);
        } else {
          openNodes.add(node.id);
        }
        render();
        if (!wasOpen) {
          fitToView();
        }
      });

      const body = document.createElement("div");
      body.className = "node-body";
      const idLabel = document.createElement("div");
      idLabel.className = "node-id";
      idLabel.textContent = node.id;
      body.appendChild(idLabel);

      const textarea = document.createElement("textarea");
      textarea.value = node.value || "";
      textarea.addEventListener("input", () => {
        scheduleSave(node.id, textarea.value);
      });
      body.appendChild(textarea);
      card.appendChild(body);

      fragment.appendChild(card);
    }
    elements.canvas.appendChild(fragment);
  }

  function scheduleSave(nodeId, value) {
    if (pendingSaves.has(nodeId)) {
      clearTimeout(pendingSaves.get(nodeId));
    }
    const timer = setTimeout(() => {
      pendingSaves.delete(nodeId);
      sendUpdate(nodeId, value);
    }, 500);
    pendingSaves.set(nodeId, timer);
  }

  async function sendUpdate(nodeId, value) {
    try {
      const response = await fetch("/api/update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: nodeId, value }),
      });
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "保存失败");
      }
      if (snapshot && snapshot[nodeId]) {
        snapshot[nodeId].value = value;
      }
      setStatus("已保存修改。", false);
    } catch (error) {
      setStatus(`保存失败：${error.message}`, true);
    }
  }

  function onPointerDown(event) {
    if (event.button !== 0) {
      return;
    }
    if (event.target.closest(".world-node")) {
      return;
    }
    dragState = {
      startX: event.clientX,
      startY: event.clientY,
      panX: pan.x,
      panY: pan.y,
      pointerId: event.pointerId,
    };
    elements.board.classList.add("is-dragging");
    elements.board.setPointerCapture(event.pointerId);
  }

  function onPointerMove(event) {
    if (!dragState) {
      return;
    }
    const dx = event.clientX - dragState.startX;
    const dy = event.clientY - dragState.startY;
    setPan(dragState.panX + dx, dragState.panY + dy);
  }

  function onPointerUp(event) {
    if (!dragState) {
      return;
    }
    if (dragState.pointerId !== undefined) {
      elements.board.releasePointerCapture(dragState.pointerId);
    }
    dragState = null;
    elements.board.classList.remove("is-dragging");
  }

  function setPan(x, y) {
    pan.x = x;
    pan.y = y;
    applyTransform();
  }

  function clampScale(value) {
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
  }

  function applyTransform() {
    if (elements.scene) {
      elements.scene.style.transform = `translate(${pan.x}px, ${pan.y}px) scale(${scale})`;
    }
  }

  function setScale(nextScale, focus) {
    const clamped = clampScale(nextScale);
    if (focus) {
      const ratio = clamped / scale;
      pan.x = focus.x - (focus.x - pan.x) * ratio;
      pan.y = focus.y - (focus.y - pan.y) * ratio;
    }
    scale = clamped;
    applyTransform();
  }

  function zoomBy(factor) {
    if (!elements.board) {
      return;
    }
    const rect = elements.board.getBoundingClientRect();
    const focus = { x: rect.width / 2, y: rect.height / 2 };
    setScale(scale * factor, focus);
  }

  function fitToView() {
    if (!elements.board || !sceneSize.width || !sceneSize.height) {
      return;
    }
    const boardWidth = elements.board.clientWidth;
    const boardHeight = elements.board.clientHeight;
    if (!boardWidth || !boardHeight) {
      return;
    }
    const margin = 40;
    const widthAvail = Math.max(boardWidth - margin * 2, 1);
    const heightAvail = Math.max(boardHeight - margin * 2, 1);
    const targetScale = clampScale(
      Math.min(widthAvail / sceneSize.width, heightAvail / sceneSize.height)
    );
    scale = targetScale;
    pan.x = (boardWidth - sceneSize.width * targetScale) / 2;
    pan.y = (boardHeight - sceneSize.height * targetScale) / 2;
    applyTransform();
  }

  return {
    init,
    load,
  };
})();

window.WorldView = WorldView;
