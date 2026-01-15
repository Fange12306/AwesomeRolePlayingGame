const WorldView = (() => {
  let elements = {};
  let snapshot = null;
  let activeTab = "macro";
  let selectedId = "";
  let pendingSaves = new Map();
  let collapsedNodes = new Set();
  let collapseInitialized = { macro: false, micro: false };
  let isEditing = false;
  let isLocked = false;
  let refreshQueued = false;
  let statusTimer = null;

  function init(config) {
    const root = document.getElementById(config.rootId);
    if (!root) {
      return;
    }

    root.innerHTML = `
      <div class="world-header">
        <div>
          <h2>世界设定</h2>
          <p class="hint">左侧切换宏观/具体设定，点击条目后可在右侧实时修改。</p>
        </div>
        <div class="world-meta">
          <div class="save-path"></div>
        </div>
      </div>
      <div class="world-status"></div>
      <div class="world-stage">
        <div class="progress">
          <div class="progress-bar"></div>
        </div>
        <div class="progress-text"></div>
      </div>
      <div class="world-panel">
        <aside class="world-list">
          <div class="world-tabs">
            <button class="world-tab is-active" data-tab="macro" type="button">世界设定</button>
            <button class="world-tab" data-tab="micro" type="button">具体设定</button>
          </div>
          <div class="world-list-scroll">
            <div class="world-list-empty">暂无条目</div>
            <ul class="world-items"></ul>
          </div>
        </aside>
        <section class="world-detail">
          <div class="world-detail-empty">请先在左侧选择一个条目。</div>
          <div class="world-detail-content">
            <div class="world-detail-head">
              <div>
                <div class="detail-title"></div>
                <div class="detail-meta"></div>
              </div>
              <button class="detail-toggle" type="button">编辑</button>
            </div>
            <div class="detail-preview"></div>
            <textarea class="detail-textarea" rows="12" placeholder="在这里补充设定内容..."></textarea>
          </div>
        </section>
      </div>
    `;

    elements = {
      root,
      status: root.querySelector(".world-status"),
      stage: root.querySelector(".world-stage"),
      stageBar: root.querySelector(".world-stage .progress-bar"),
      stageText: root.querySelector(".world-stage .progress-text"),
      savePath: root.querySelector(".save-path"),
      tabs: Array.from(root.querySelectorAll(".world-tab")),
      list: root.querySelector(".world-items"),
      listEmpty: root.querySelector(".world-list-empty"),
      detailEmpty: root.querySelector(".world-detail-empty"),
      detailContent: root.querySelector(".world-detail-content"),
      detailTitle: root.querySelector(".detail-title"),
      detailMeta: root.querySelector(".detail-meta"),
      detailToggle: root.querySelector(".detail-toggle"),
      detailPreview: root.querySelector(".detail-preview"),
      detailTextarea: root.querySelector(".detail-textarea"),
    };

    elements.tabs.forEach((button) => {
      button.addEventListener("click", () => {
        setActiveTab(button.dataset.tab || "macro");
      });
    });

    if (elements.list) {
      elements.list.addEventListener("click", (event) => {
        const toggle = event.target.closest(".world-item-toggle");
        if (toggle) {
          const nodeId = toggle.dataset.nodeId || toggle.closest(".world-item")?.dataset.nodeId;
          if (nodeId) {
            toggleCollapse(nodeId);
          }
          return;
        }
        const main = event.target.closest(".world-item-main");
        if (!main) {
          return;
        }
        const nodeId = main.dataset.nodeId || main.closest(".world-item")?.dataset.nodeId;
        if (nodeId) {
          selectNode(nodeId);
        }
      });
    }

    if (elements.detailTextarea) {
      elements.detailTextarea.addEventListener("input", () => {
        if (!selectedId || isLocked) {
          return;
        }
        scheduleSave(selectedId, elements.detailTextarea.value);
        updatePreview(elements.detailTextarea.value);
      });
    }

    if (elements.detailToggle) {
      elements.detailToggle.addEventListener("click", () => {
        setEditing(!isEditing);
      });
    }

    if (elements.detailPreview) {
      elements.detailPreview.addEventListener("click", () => {
        setEditing(true);
      });
    }
  }

  async function load() {
    try {
      const response = await fetch("/api/world");
      const data = await response.json();
      if (!data.ok) {
        throw new Error("暂无世界数据。");
      }
      snapshot = data.snapshot;
      pendingSaves.forEach((timer) => clearTimeout(timer));
      pendingSaves.clear();
      collapsedNodes.clear();
      collapseInitialized = { macro: false, micro: false };
      if (elements.savePath) {
        elements.savePath.textContent = data.save_path
          ? `存档：${data.save_path}`
          : "";
      }
      render();
      startStatusPolling();
      setStatus("世界设定已加载，修改会自动保存。", false);
    } catch (error) {
      snapshot = null;
      stopStatusPolling();
      setLocked(false);
      if (elements.list) {
        elements.list.innerHTML = "";
      }
      if (elements.savePath) {
        elements.savePath.textContent = "";
      }
      if (elements.detailTextarea) {
        elements.detailTextarea.value = "";
      }
      if (elements.detailPreview) {
        elements.detailPreview.innerHTML = "";
      }
      selectedId = "";
      isEditing = false;
      renderDetail();
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

  function startStatusPolling() {
    if (statusTimer) {
      clearInterval(statusTimer);
    }
    statusTimer = setInterval(fetchGenerationStatus, 800);
    fetchGenerationStatus();
  }

  function stopStatusPolling() {
    if (statusTimer) {
      clearInterval(statusTimer);
      statusTimer = null;
    }
  }

  async function fetchGenerationStatus() {
    try {
      const response = await fetch("/api/world/status");
      const data = await response.json();
      if (!data.ok) {
        return;
      }
      applyGenerationStatus(data);
    } catch (error) {
      // Ignore polling failures.
    }
  }

  function applyGenerationStatus(data) {
    if (!elements.stage || !elements.stageBar || !elements.stageText) {
      return;
    }
    const status = data.status || "idle";
    const phase = data.phase || "";
    const running = status === "running";
    const showStage = running && phase === "micro";
    if (showStage) {
      const total = data.stage_total || data.micro_total || 0;
      const completed = data.stage_completed || 0;
      const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
      elements.stage.classList.add("is-active");
      elements.stageBar.style.width = `${percent}%`;
      elements.stageText.textContent = `第二阶段进度 ${completed}/${total} (${percent}%)`;
    } else {
      elements.stage.classList.remove("is-active");
      elements.stageBar.style.width = "0%";
      elements.stageText.textContent = "";
    }
    setLocked(running && phase !== "done");
  }

  function setLocked(locked) {
    if (isLocked === locked) {
      return;
    }
    isLocked = locked;
    if (elements.root) {
      elements.root.classList.toggle("is-locked", locked);
    }
    if (elements.detailTextarea) {
      elements.detailTextarea.disabled = locked;
    }
    if (elements.detailToggle) {
      elements.detailToggle.disabled = locked;
    }
    if (locked && isEditing) {
      isEditing = false;
    }
    updateDetailMode();
  }

  function setActiveTab(tab) {
    const nextTab = tab === "micro" ? "micro" : "macro";
    if (nextTab === activeTab) {
      return;
    }
    activeTab = nextTab;
    render();
  }

  function render() {
    if (!snapshot || !elements.list) {
      return;
    }
    elements.tabs.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.tab === activeTab);
    });
    ensureCollapseInitialized();
    renderList();
    renderDetail();
  }

  function ensureCollapseInitialized() {
    if (collapseInitialized[activeTab]) {
      return;
    }
    const rootId = activeTab === "micro" ? "micro" : "macro";
    const root = snapshot?.[rootId];
    if (!root) {
      return;
    }
    const walk = (nodeId) => {
      const node = snapshot?.[nodeId];
      if (!node) {
        return;
      }
      if ((node.children || []).length) {
        collapsedNodes.add(nodeId);
      }
      (node.children || []).forEach((childId) => walk(childId));
    };
    (root.children || []).forEach((childId) => walk(childId));
    collapseInitialized[activeTab] = true;
  }

  function buildNodeList(rootId) {
    const root = snapshot?.[rootId];
    if (!root) {
      return [];
    }
    const items = [];
    const walk = (nodeId, depth) => {
      const node = snapshot?.[nodeId];
      if (!node) {
        return;
      }
      const children = node.children || [];
      items.push({
        id: nodeId,
        key: node.key || node.title || nodeId,
        value: node.value || "",
        depth,
        childCount: children.length,
        isCollapsed: collapsedNodes.has(nodeId),
      });
      if (!collapsedNodes.has(nodeId)) {
        children.forEach((childId) => walk(childId, depth + 1));
      }
    };
    (root.children || []).forEach((childId) => walk(childId, 0));
    return items;
  }

  function renderList() {
    if (!elements.list) {
      return;
    }
    const items = buildNodeList(activeTab === "micro" ? "micro" : "macro");
    elements.list.innerHTML = "";
    if (!items.length) {
      if (elements.listEmpty) {
        elements.listEmpty.style.display = "block";
      }
      selectedId = "";
      return;
    }
    if (elements.listEmpty) {
      elements.listEmpty.style.display = "none";
    }
    if (!selectedId || !items.some((item) => item.id === selectedId)) {
      selectedId = items[0].id;
      isEditing = false;
    }
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const li = document.createElement("li");
      const row = document.createElement("div");
      row.className = "world-item";
      row.dataset.nodeId = item.id;
      row.style.paddingLeft = `${16 + item.depth * 18}px`;
      if (item.id === selectedId) {
        row.classList.add("is-active");
      }

      if (item.childCount > 0) {
        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "world-item-toggle";
        toggle.dataset.nodeId = item.id;
        toggle.textContent = item.isCollapsed ? "+" : "-";
        toggle.title = item.isCollapsed ? "展开" : "收起";
        row.appendChild(toggle);
      } else {
        const spacer = document.createElement("span");
        spacer.className = "world-item-spacer";
        row.appendChild(spacer);
      }

      const main = document.createElement("button");
      main.type = "button";
      main.className = "world-item-main";
      main.dataset.nodeId = item.id;

      const title = document.createElement("div");
      title.className = "world-item-title";
      title.textContent = item.key;

      const meta = document.createElement("div");
      meta.className = "world-item-meta";
      meta.textContent = item.id;

      main.appendChild(title);
      main.appendChild(meta);
      row.appendChild(main);
      li.appendChild(row);
      fragment.appendChild(li);
    }
    elements.list.appendChild(fragment);
  }

  function renderDetail() {
    if (!elements.detailContent || !elements.detailEmpty || !elements.detailTextarea) {
      return;
    }
    const node = snapshot?.[selectedId];
    if (!node) {
      elements.detailEmpty.style.display = "flex";
      elements.detailContent.style.display = "none";
      elements.detailTextarea.value = "";
      if (elements.detailPreview) {
        elements.detailPreview.innerHTML = "";
      }
      return;
    }
    elements.detailEmpty.style.display = "none";
    elements.detailContent.style.display = "flex";
    if (elements.detailTitle) {
      elements.detailTitle.textContent = node.key || node.title || node.identifier || selectedId;
    }
    if (elements.detailMeta) {
      elements.detailMeta.textContent = selectedId;
    }
    elements.detailTextarea.value = node.value || "";
    updatePreview(node.value || "");
    updateDetailMode();
  }

  function selectNode(nodeId) {
    if (!nodeId || nodeId === selectedId) {
      return;
    }
    const current = elements.list?.querySelector(".world-item.is-active");
    if (current) {
      current.classList.remove("is-active");
    }
    const next = elements.list?.querySelector(`[data-node-id="${nodeId}"]`);
    if (next) {
      next.classList.add("is-active");
    }
    selectedId = nodeId;
    isEditing = false;
    renderDetail();
  }

  function toggleCollapse(nodeId) {
    if (!nodeId) {
      return;
    }
    if (collapsedNodes.has(nodeId)) {
      collapsedNodes.delete(nodeId);
    } else {
      collapsedNodes.add(nodeId);
    }
    render();
  }

  function setEditing(next) {
    if (!selectedId || isLocked) {
      return;
    }
    isEditing = Boolean(next);
    if (!isEditing) {
      updatePreview(elements.detailTextarea?.value || "");
    }
    updateDetailMode();
    if (!isEditing) {
      flushQueuedRefresh();
    }
    if (isEditing && elements.detailTextarea) {
      elements.detailTextarea.focus();
      elements.detailTextarea.selectionStart = elements.detailTextarea.value.length;
      elements.detailTextarea.selectionEnd = elements.detailTextarea.value.length;
    }
  }

  function updateDetailMode() {
    if (!elements.detailPreview || !elements.detailTextarea || !elements.detailToggle) {
      return;
    }
    const showEditor = isEditing && Boolean(selectedId) && !isLocked;
    elements.detailPreview.style.display = showEditor ? "none" : "block";
    elements.detailTextarea.style.display = showEditor ? "block" : "none";
    elements.detailToggle.textContent = showEditor ? "预览" : "编辑";
    elements.detailTextarea.disabled = isLocked;
    elements.detailToggle.disabled = isLocked;
  }

  function updatePreview(value) {
    if (!elements.detailPreview) {
      return;
    }
    const trimmed = (value || "").trim();
    if (!trimmed) {
      elements.detailPreview.innerHTML = '<div class="detail-preview-empty">暂无内容，点击开始编辑。</div>';
      return;
    }
    elements.detailPreview.innerHTML = renderMarkdown(value);
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function renderInlineMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    html = html.replace(/\*([^*]+)\*/g, "<em>$1</em>");
    html = html.replace(/_([^_]+)_/g, "<em>$1</em>");
    html = html.replace(/\n/g, "<br>");
    return html;
  }

  function renderMarkdown(text) {
    const chunks = String(text || "").split(/```/);
    const output = [];
    for (let index = 0; index < chunks.length; index += 1) {
      const chunk = chunks[index];
      if (index % 2 === 1) {
        output.push(`<pre><code>${escapeHtml(chunk.trim())}</code></pre>`);
      } else {
        output.push(renderMarkdownBlocks(chunk));
      }
    }
    return output.join("");
  }

  function renderMarkdownBlocks(text) {
    const lines = String(text || "").split(/\r?\n/);
    const output = [];
    let paragraph = [];
    let blockquote = [];
    let inUl = false;
    let inOl = false;

    const closeLists = () => {
      if (inUl) {
        output.push("</ul>");
        inUl = false;
      }
      if (inOl) {
        output.push("</ol>");
        inOl = false;
      }
    };

    const flushParagraph = () => {
      if (!paragraph.length) {
        return;
      }
      output.push(`<p>${renderInlineMarkdown(paragraph.join("\n"))}</p>`);
      paragraph = [];
    };

    const flushBlockquote = () => {
      if (!blockquote.length) {
        return;
      }
      output.push(`<blockquote><p>${renderInlineMarkdown(blockquote.join("\n"))}</p></blockquote>`);
      blockquote = [];
    };

    for (const line of lines) {
      const trimmed = line.trim();
      const headingMatch = trimmed.match(/^(#{1,4})\s+(.*)$/);
      const ulMatch = trimmed.match(/^[-*+]\s+(.*)$/);
      const olMatch = trimmed.match(/^\d+\.\s+(.*)$/);
      const isQuote = trimmed.startsWith(">");

      if (!trimmed) {
        flushParagraph();
        flushBlockquote();
        closeLists();
        continue;
      }

      if (headingMatch) {
        flushParagraph();
        flushBlockquote();
        closeLists();
        const level = headingMatch[1].length;
        output.push(`<h${level}>${renderInlineMarkdown(headingMatch[2])}</h${level}>`);
        continue;
      }

      if (ulMatch) {
        flushParagraph();
        flushBlockquote();
        if (inOl) {
          output.push("</ol>");
          inOl = false;
        }
        if (!inUl) {
          output.push("<ul>");
          inUl = true;
        }
        output.push(`<li>${renderInlineMarkdown(ulMatch[1])}</li>`);
        continue;
      }

      if (olMatch) {
        flushParagraph();
        flushBlockquote();
        if (inUl) {
          output.push("</ul>");
          inUl = false;
        }
        if (!inOl) {
          output.push("<ol>");
          inOl = true;
        }
        output.push(`<li>${renderInlineMarkdown(olMatch[1])}</li>`);
        continue;
      }

      if (isQuote) {
        flushParagraph();
        closeLists();
        blockquote.push(trimmed.replace(/^>\s?/, ""));
        continue;
      }

      paragraph.push(line);
    }

    flushParagraph();
    flushBlockquote();
    closeLists();

    return output.join("");
  }

  function scheduleSave(nodeId, value) {
    if (isLocked) {
      return;
    }
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
    } finally {
      flushQueuedRefresh();
    }
  }

  function canReload() {
    return !isEditing && pendingSaves.size === 0;
  }

  function flushQueuedRefresh() {
    if (!refreshQueued || !canReload()) {
      return;
    }
    refreshQueued = false;
    load();
  }

  function requestRefresh() {
    if (canReload()) {
      load();
      return;
    }
    refreshQueued = true;
  }

  return {
    init,
    load,
    requestRefresh,
  };
})();

window.WorldView = WorldView;
