const AgentTestView = (() => {
  let elements = {};
  let runCount = 0;

  function init(config) {
    const root = document.getElementById(config.rootId);
    if (!root) {
      return;
    }

    root.innerHTML = `
      <div class="agent-shell">
        <div class="agent-header">
          <div>
            <h2>Agent 测试台</h2>
            <p class="hint">输入剧情片段，查看 GameAgent 的决策与对应操作。</p>
          </div>
          <div class="agent-meta">
            <div class="agent-meta-row">
              <span class="agent-meta-label">世界快照</span>
              <span class="agent-meta-value" data-role="world-snapshot">自动选择最新存档</span>
            </div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">角色快照</span>
              <span class="agent-meta-value" data-role="character-snapshot">自动选择最新存档</span>
            </div>
            <div class="agent-meta-row">
              <span class="agent-meta-label">角色数量</span>
              <span class="agent-meta-value" data-role="character-count">--</span>
            </div>
          </div>
        </div>

        <div class="agent-operations">
          <div class="agent-operations-header">
            <h3>将要执行的操作</h3>
            <span class="agent-status" data-role="status">等待输入</span>
          </div>
          <div class="agent-list" data-role="list">
            <div class="agent-empty" data-role="empty">暂无操作记录。</div>
          </div>
        </div>

        <div class="agent-input-panel">
          <label class="field">
            <span>剧情输入</span>
            <textarea
              data-role="input"
              rows="5"
              placeholder="例：议会被推翻，旧贵族流亡，主角被任命为新的执政官。"
            ></textarea>
          </label>
          <div class="actions">
            <button class="btn primary" data-role="run">生成操作</button>
            <button class="btn ghost" data-role="clear">清空记录</button>
          </div>
          <div class="agent-tip">快捷键：Ctrl / Command + Enter 发送。</div>
        </div>
      </div>
    `;

    elements = {
      root,
      status: root.querySelector("[data-role='status']"),
      list: root.querySelector("[data-role='list']"),
      empty: root.querySelector("[data-role='empty']"),
      runBtn: root.querySelector("[data-role='run']"),
      clearBtn: root.querySelector("[data-role='clear']"),
      input: root.querySelector("[data-role='input']"),
      worldSnapshot: root.querySelector("[data-role='world-snapshot']"),
      characterSnapshot: root.querySelector("[data-role='character-snapshot']"),
      characterCount: root.querySelector("[data-role='character-count']"),
    };

    if (elements.runBtn) {
      elements.runBtn.addEventListener("click", runPlan);
    }
    if (elements.clearBtn) {
      elements.clearBtn.addEventListener("click", clearLog);
    }
    if (elements.input) {
      elements.input.addEventListener("keydown", handleInputKeydown);
    }
  }

  function load() {
    setStatus("等待输入", false);
  }

  function handleInputKeydown(event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      runPlan();
    }
  }

  function setStatus(message, isError) {
    if (!elements.status) {
      return;
    }
    elements.status.textContent = message;
    elements.status.classList.toggle("is-error", Boolean(isError));
  }

  function updateContext(context) {
    if (!context) {
      return;
    }
    if (elements.worldSnapshot) {
      elements.worldSnapshot.textContent = context.world_snapshot
        ? context.world_snapshot
        : "未找到世界存档";
    }
    if (elements.characterSnapshot) {
      elements.characterSnapshot.textContent = context.character_snapshot
        ? context.character_snapshot
        : "未找到角色存档";
    }
    if (elements.characterCount) {
      if (typeof context.character_count === "number") {
        elements.characterCount.textContent = String(context.character_count);
      } else {
        elements.characterCount.textContent = "--";
      }
    }
  }

  async function runPlan() {
    if (!elements.input) {
      return;
    }
    const text = elements.input.value.trim();
    if (!text) {
      setStatus("请输入剧情文本。", true);
      return;
    }

    const originalLabel = elements.runBtn ? elements.runBtn.textContent : "";
    if (elements.runBtn) {
      elements.runBtn.disabled = true;
      elements.runBtn.textContent = "分析中...";
    }
    setStatus("正在分析...", false);

    try {
      const response = await fetch("/api/game/plan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, apply: true }),
      });
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "请求失败");
      }
      renderEntry(data, text);
      updateContext(data.context);
      refreshViews(data.applied);
      if (data.applied && (data.applied.world || data.applied.character)) {
        setStatus("已更新存档并刷新视图。", false);
      } else {
        setStatus("操作已生成。", false);
      }
    } catch (error) {
      setStatus(`分析失败：${error.message}`, true);
    } finally {
      if (elements.runBtn) {
        elements.runBtn.disabled = false;
        elements.runBtn.textContent = originalLabel || "生成操作";
      }
    }
  }

  function clearLog() {
    runCount = 0;
    if (elements.list) {
      elements.list.innerHTML = "";
      const empty = document.createElement("div");
      empty.className = "agent-empty";
      empty.dataset.role = "empty";
      empty.textContent = "暂无操作记录。";
      elements.list.appendChild(empty);
      elements.empty = empty;
    }
    if (elements.input) {
      elements.input.value = "";
    }
    setStatus("等待输入", false);
  }

  function refreshViews(applied) {
    const shouldRefreshWorld = !applied || applied.world;
    const shouldRefreshCharacter = !applied || applied.character;
    if (shouldRefreshWorld && window.WorldView) {
      if (window.WorldView.requestRefresh) {
        window.WorldView.requestRefresh();
      } else if (window.WorldView.load) {
        window.WorldView.load();
      }
    }
    if (shouldRefreshCharacter && window.CharacterView && window.CharacterView.load) {
      window.CharacterView.load();
    }
  }

  function renderEntry(data, inputText) {
    if (!elements.list) {
      return;
    }
    runCount += 1;
    if (elements.empty) {
      elements.empty.remove();
      elements.empty = null;
    }

    const decision = data.decision || {};
    const actions = Array.isArray(data.actions) ? data.actions : [];
    const worldAction = actions.find((item) => item.agent === "world");
    const characterAction = actions.find((item) => item.agent === "character");
    const updateWorld = Boolean(decision.update_world);
    const updateCharacters = Boolean(decision.update_characters);

    const entry = document.createElement("article");
    entry.className = "agent-card";

    const header = document.createElement("div");
    header.className = "agent-card-header";
    const title = document.createElement("div");
    title.className = "agent-card-title";
    title.textContent = `记录 ${runCount}`;
    const time = document.createElement("div");
    time.className = "agent-card-time";
    time.textContent = new Date().toLocaleTimeString();
    header.appendChild(title);
    header.appendChild(time);

    const inputBlock = document.createElement("div");
    inputBlock.className = "agent-card-input";
    inputBlock.textContent = inputText;

    const grid = document.createElement("div");
    grid.className = "agent-card-grid";
    grid.appendChild(
      buildBlock("GameAgent", "agent-chip-game", [
        { text: `世界: ${updateWorld ? "更新" : "不更新"}` },
        { text: `角色: ${updateCharacters ? "更新" : "不更新"}` },
        ...(decision.reason
          ? [{ text: `原因: ${decision.reason}`, muted: true }]
          : []),
      ])
    );
    grid.appendChild(
      buildBlock(
        "WorldAgent",
        "agent-chip-world",
        updateWorld
          ? buildActionLines(worldAction, "世界操作未生成")
          : [{ text: "不更新世界", muted: true }]
      )
    );
    grid.appendChild(
      buildBlock(
        "CharacterAgent",
        "agent-chip-character",
        updateCharacters
          ? buildActionLines(characterAction, "角色操作未生成")
          : [{ text: "不更新角色", muted: true }]
      )
    );

    entry.appendChild(header);
    entry.appendChild(inputBlock);
    entry.appendChild(grid);

    elements.list.prepend(entry);
  }

  function buildActionLines(action, emptyText) {
    if (!action) {
      return [{ text: emptyText, muted: true }];
    }
    const parts = [action.action, action.target].filter(Boolean);
    const actionText = parts.length ? parts.join(" ") : "未提供操作";
    const lines = [{ text: `操作: ${actionText}` }];
    if (action.label) {
      lines.push({ text: `目标: ${action.label}`, muted: true });
    }
    return lines;
  }

  function buildBlock(label, chipClass, lines) {
    const block = document.createElement("div");
    block.className = "agent-card-block";
    const chip = document.createElement("div");
    chip.className = `agent-chip ${chipClass}`;
    chip.textContent = label;
    block.appendChild(chip);
    lines.forEach((line) => {
      const row = document.createElement("div");
      row.className = "agent-line";
      if (line.muted) {
        row.classList.add("is-muted");
      }
      row.textContent = line.text;
      block.appendChild(row);
    });
    return block;
  }

  return {
    init,
    load,
  };
})();

window.AgentTestView = AgentTestView;
