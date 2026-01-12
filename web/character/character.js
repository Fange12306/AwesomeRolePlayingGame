const CharacterView = (() => {
  let elements = {};
  let snapshot = null;
  let activeId = "";
  let filterText = "";
  let currentPath = "";

  function init(config) {
    const root = document.getElementById(config.rootId);
    if (!root) {
      return;
    }

    root.innerHTML = `
      <div class="character-header">
        <div>
          <h2>角色节点</h2>
          <p class="hint">选择角色快照，点击卡片查看详情与关系。</p>
        </div>
        <div class="character-tools">
          <select class="character-select"></select>
          <button class="character-tool" data-action="refresh" type="button">刷新</button>
        </div>
      </div>
      <div class="character-path"></div>
      <div class="character-status"></div>
      <div class="character-layout">
        <div class="character-column">
          <div class="character-filters">
            <input class="character-search" type="search" placeholder="搜索角色 ID / 名称 / 阵营..." />
            <div class="character-count"></div>
          </div>
          <div class="character-list"></div>
        </div>
        <div class="character-detail"></div>
      </div>
    `;

    elements = {
      root,
      select: root.querySelector(".character-select"),
      refreshBtn: root.querySelector('[data-action="refresh"]'),
      status: root.querySelector(".character-status"),
      list: root.querySelector(".character-list"),
      detail: root.querySelector(".character-detail"),
      search: root.querySelector(".character-search"),
      count: root.querySelector(".character-count"),
      path: root.querySelector(".character-path"),
    };

    if (elements.select) {
      elements.select.addEventListener("change", () => {
        const nextPath = elements.select.value;
        currentPath = nextPath;
        if (nextPath) {
          loadSnapshot(nextPath);
        }
      });
    }

    if (elements.refreshBtn) {
      elements.refreshBtn.addEventListener("click", loadSnapshots);
    }

    if (elements.search) {
      elements.search.addEventListener("input", () => {
        filterText = elements.search.value.trim().toLowerCase();
        renderList();
        renderDetail();
      });
    }
  }

  async function load() {
    if (!elements.root) {
      return;
    }
    await loadSnapshots();
  }

  async function loadSnapshots() {
    if (!elements.select || !elements.refreshBtn) {
      return;
    }
    elements.select.disabled = true;
    elements.refreshBtn.disabled = true;
    setStatus("正在读取角色快照...", false);
    try {
      const response = await fetch("/api/characters/snapshots");
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "读取失败");
      }
      const snapshots = Array.isArray(data.snapshots) ? data.snapshots : [];
      renderSnapshotOptions(snapshots);
      if (!snapshots.length) {
        snapshot = null;
        activeId = "";
        render();
        setStatus("暂无角色快照，请先生成角色。", true);
        return;
      }
      const nextPath = elements.select.value;
      if (nextPath) {
        await loadSnapshot(nextPath);
      }
    } catch (error) {
      snapshot = null;
      activeId = "";
      render();
      setStatus(`读取失败：${error.message}`, true);
    } finally {
      elements.select.disabled = false;
      elements.refreshBtn.disabled = false;
    }
  }

  function renderSnapshotOptions(snapshots) {
    if (!elements.select) {
      return;
    }
    const previous = currentPath;
    elements.select.innerHTML = "";
    if (!snapshots.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无角色快照";
      elements.select.appendChild(option);
      currentPath = "";
      return;
    }
    snapshots.forEach((item, index) => {
      const option = document.createElement("option");
      const value = item.path || item.full_path || "";
      option.value = value;
      option.textContent = item.name || item.path || `快照 ${index + 1}`;
      if (previous && value === previous) {
        option.selected = true;
      }
      elements.select.appendChild(option);
    });
    if (!elements.select.value) {
      elements.select.selectedIndex = 0;
    }
    currentPath = elements.select.value;
  }

  async function loadSnapshot(path) {
    if (!path) {
      return;
    }
    currentPath = path;
    setStatus("正在加载角色快照...", false);
    try {
      const response = await fetch(`/api/characters?path=${encodeURIComponent(path)}`);
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "加载失败");
      }
      snapshot = data.snapshot || null;
      activeId = "";
      render();
      setStatus("角色快照已加载。", false);
    } catch (error) {
      snapshot = null;
      activeId = "";
      render();
      setStatus(`加载失败：${error.message}`, true);
    }
  }

  function setStatus(message, isError) {
    if (!elements.status) {
      return;
    }
    elements.status.textContent = message;
    elements.status.classList.toggle("is-error", Boolean(isError));
  }

  function render() {
    renderMeta();
    renderList();
    renderDetail();
  }

  function renderMeta() {
    if (!elements.path) {
      return;
    }
    const parts = [];
    if (currentPath) {
      parts.push(`角色快照：${currentPath}`);
    }
    const worldPath = snapshot?.world_snapshot_path;
    if (worldPath) {
      parts.push(`世界快照：${worldPath}`);
    }
    elements.path.textContent = parts.join(" · ");
  }

  function renderList() {
    if (!elements.list || !elements.count) {
      return;
    }
    elements.list.innerHTML = "";
    const records = normalizeRecords(snapshot?.characters);
    const filtered = filterText
      ? records.filter((record) => matchFilter(record, filterText))
      : records;
    elements.count.textContent = records.length
      ? `${filtered.length}/${records.length}`
      : "0";

    if (!filtered.length) {
      const empty = document.createElement("div");
      empty.className = "character-empty";
      empty.textContent = records.length
        ? "没有匹配的角色。"
        : "暂无角色数据。";
      elements.list.appendChild(empty);
      if (activeId && !filtered.find((item) => item.id === activeId)) {
        activeId = "";
      }
      return;
    }

    if (!activeId) {
      activeId = filtered[0].id;
    }

    const fragment = document.createDocumentFragment();
    filtered.forEach((record) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = "character-card";
      card.classList.toggle("is-active", record.id === activeId);
      card.addEventListener("click", () => {
        activeId = record.id;
        renderList();
        renderDetail();
      });

      const title = document.createElement("div");
      title.className = "character-card-title";
      title.textContent = record.name || record.id || "未命名角色";

      const meta = document.createElement("div");
      meta.className = "character-card-meta";
      meta.textContent = record.id || "";

      const summary = document.createElement("div");
      summary.className = "character-card-summary";
      summary.textContent = record.summary || "暂无概述";

      const tags = document.createElement("div");
      tags.className = "character-card-tags";
      record.tags.forEach((tagText) => {
        const tag = document.createElement("span");
        tag.className = "character-tag";
        tag.textContent = tagText;
        tags.appendChild(tag);
      });

      card.appendChild(title);
      card.appendChild(meta);
      card.appendChild(summary);
      if (record.tags.length) {
        card.appendChild(tags);
      }
      fragment.appendChild(card);
    });
    elements.list.appendChild(fragment);
  }

  function renderDetail() {
    if (!elements.detail) {
      return;
    }
    elements.detail.innerHTML = "";
    const records = normalizeRecords(snapshot?.characters);
    const record = records.find((item) => item.id === activeId);
    if (!record) {
      const empty = document.createElement("div");
      empty.className = "character-empty";
      empty.textContent = "选择一个角色查看详情。";
      elements.detail.appendChild(empty);
      return;
    }

    const head = document.createElement("div");
    head.className = "detail-head";

    const headInfo = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = record.name || record.id;
    const meta = document.createElement("div");
    meta.className = "detail-meta";
    meta.textContent = [
      record.id && `ID: ${record.id}`,
      record.regionId && `区域: ${record.regionId}`,
      record.polityId && `政体: ${record.polityId}`,
    ]
      .filter(Boolean)
      .join(" · ");
    headInfo.appendChild(name);
    if (meta.textContent) {
      headInfo.appendChild(meta);
    }

    const tagWrap = document.createElement("div");
    tagWrap.className = "detail-tags";
    record.tags.forEach((tagText) => {
      const tag = document.createElement("span");
      tag.className = "character-tag";
      tag.textContent = tagText;
      tagWrap.appendChild(tag);
    });

    head.appendChild(headInfo);
    if (record.tags.length) {
      head.appendChild(tagWrap);
    }
    elements.detail.appendChild(head);

    appendSection(elements.detail, "简述", record.summary);
    appendSection(elements.detail, "背景", record.background);
    appendSection(elements.detail, "动机", record.motivation);
    appendSection(elements.detail, "冲突", record.conflict);
    appendSection(elements.detail, "能力", record.abilities);
    appendSection(elements.detail, "弱点", record.weaknesses);
    appendSection(elements.detail, "关系倾向", record.relationships);
    appendSection(elements.detail, "剧情钩子", record.hooks);

    const relationItems = buildRelationItems(record.id);
    appendListSection(elements.detail, "角色关系", relationItems);

    const locationItems = buildLocationItems(record.id);
    appendListSection(elements.detail, "地点关系", locationItems);
  }

  function appendSection(root, title, content) {
    if (!content) {
      return;
    }
    const section = document.createElement("div");
    section.className = "detail-section";
    const label = document.createElement("div");
    label.className = "detail-label";
    label.textContent = title;
    const body = document.createElement("div");
    body.className = "detail-content";
    body.textContent = content;
    section.appendChild(label);
    section.appendChild(body);
    root.appendChild(section);
  }

  function appendListSection(root, title, items) {
    const section = document.createElement("div");
    section.className = "detail-section";
    const label = document.createElement("div");
    label.className = "detail-label";
    label.textContent = title;
    section.appendChild(label);
    const list = document.createElement("div");
    list.className = "detail-list";
    if (!items.length) {
      list.textContent = "无";
    } else {
      items.forEach((item) => {
        const row = document.createElement("div");
        row.className = "detail-item";
        const head = document.createElement("div");
        head.className = "detail-item-title";
        head.textContent = item.title;
        row.appendChild(head);
        if (item.detail) {
          const detail = document.createElement("div");
          detail.className = "detail-item-note";
          detail.textContent = item.detail;
          row.appendChild(detail);
        }
        list.appendChild(row);
      });
    }
    section.appendChild(list);
    root.appendChild(section);
  }

  function buildRelationItems(characterId) {
    const relations = Array.isArray(snapshot?.relations) ? snapshot.relations : [];
    const items = relations
      .filter(
        (item) =>
          item
          && (item.source_id === characterId || item.target_id === characterId)
      )
      .map((item) => {
        const source = item.source_id || "";
        const target = item.target_id || "";
        const other = source === characterId ? target : source;
        const direction = source === characterId ? "→" : "←";
        const title = `${direction} ${other || "未知角色"}`;
        const detailParts = [];
        if (item.type) {
          detailParts.push(item.type);
        }
        if (item.stance) {
          detailParts.push(item.stance);
        }
        if (item.intensity !== undefined) {
          detailParts.push(`强度 ${item.intensity}`);
        }
        if (item.note) {
          detailParts.push(item.note);
        }
        return { title, detail: detailParts.join(" · ") };
      });
    return items;
  }

  function buildLocationItems(characterId) {
    const edges = Array.isArray(snapshot?.character_location_edges)
      ? snapshot.character_location_edges
      : [];
    const items = edges
      .filter((item) => item && item.character_id === characterId)
      .map((item) => {
        const title = `${item.location_id || "未知地点"} (${item.relation_type || "link"})`;
        const detailParts = [];
        if (item.location_type) {
          detailParts.push(item.location_type);
        }
        if (item.intensity !== undefined) {
          detailParts.push(`强度 ${item.intensity}`);
        }
        if (item.since) {
          detailParts.push(item.since);
        }
        if (item.cause) {
          detailParts.push(item.cause);
        }
        return { title, detail: detailParts.join(" · ") };
      });
    return items;
  }

  function normalizeRecords(records) {
    if (!Array.isArray(records)) {
      return [];
    }
    return records.map((record) => {
      const profile = normalizeProfile(record?.profile);
      const id = record?.id || record?.identifier || "";
      return {
        id,
        name: profile.name || "",
        summary: truncate(profile.summary || profile.background || "", 90),
        background: profile.background || "",
        motivation: profile.motivation || "",
        conflict: profile.conflict || "",
        abilities: profile.abilities || "",
        weaknesses: profile.weaknesses || "",
        relationships: profile.relationships || "",
        hooks: profile.hooks || "",
        regionId: record?.region_id || "",
        polityId: record?.polity_id || "",
        tags: [
          profile.tier && `层级:${profile.tier}`,
          profile.faction && `阵营:${profile.faction}`,
          profile.profession && `职业:${profile.profession}`,
          profile.species && `种族:${profile.species}`,
        ].filter(Boolean),
      };
    });
  }

  function normalizeProfile(profile) {
    if (profile && typeof profile === "object" && !Array.isArray(profile)) {
      return profile;
    }
    if (typeof profile === "string") {
      return { summary: profile };
    }
    return {};
  }

  function matchFilter(record, text) {
    const haystack = [
      record.id,
      record.name,
      record.summary,
      record.tags.join(" "),
      record.regionId,
      record.polityId,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(text);
  }

  function truncate(text, limit) {
    if (!text) {
      return "";
    }
    if (text.length <= limit) {
      return text;
    }
    return `${text.slice(0, limit - 1)}…`;
  }

  return {
    init,
    load,
  };
})();

window.CharacterView = CharacterView;
