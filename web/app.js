const screens = {
  landing: document.getElementById("screen-landing"),
  generator: document.getElementById("screen-generator"),
  character: document.getElementById("screen-character"),
  app: document.getElementById("screen-app"),
};

const nav = document.getElementById("app-nav");
const navButtons = Array.from(document.querySelectorAll(".nav-btn"));
const appPages = Array.from(document.querySelectorAll(".app-page"));

const promptInput = document.getElementById("world-prompt");
const generateBtn = document.getElementById("btn-generate");
const importBtn = document.getElementById("btn-import");
const fileInput = document.getElementById("world-file");
const progressBar = document.getElementById("progress-bar");
const progressText = document.getElementById("progress-text");
const skipWorldBtn = document.getElementById("btn-skip-world");
const skipCharacterBtn = document.getElementById("btn-skip-character");
const characterWorldSelect = document.getElementById("character-world");
const characterTotalInput = document.getElementById("character-total");
const characterPitchInput = document.getElementById("character-pitch");
const characterGenerateBtn = document.getElementById("btn-character-generate");
const characterRefreshBtn = document.getElementById("btn-character-refresh");
const characterProgressBar = document.getElementById("character-progress-bar");
const characterProgressText = document.getElementById("character-progress-text");

let pollTimer = null;
let characterPollTimer = null;
let latestWorldSavePath = "";
let worldStageReady = false;

function showScreen(name) {
  Object.entries(screens).forEach(([key, node]) => {
    node.classList.toggle("active", key === name);
  });
  nav.classList.toggle("is-hidden", name !== "app");
  if (name === "character") {
    loadWorldSnapshots();
  }
}

function showPage(name) {
  appPages.forEach((page) => {
    page.classList.toggle("active", page.dataset.page === name);
  });
  navButtons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.page === name);
  });
  if (name === "world" && window.WorldView) {
    window.WorldView.load();
  }
  if (name === "character" && window.CharacterView) {
    window.CharacterView.load();
  }
}

function setProgress(completed, total) {
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  progressBar.style.width = `${percent}%`;
  progressText.textContent = `进度 ${completed}/${total} (${percent}%)`;
}

function setProgressMessage(message) {
  progressText.textContent = message;
}

function setCharacterProgress(completed, total) {
  if (!characterProgressBar || !characterProgressText) {
    return;
  }
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  characterProgressBar.style.width = `${percent}%`;
  characterProgressText.textContent = `进度 ${completed}/${total} (${percent}%)`;
}

function setCharacterMessage(message) {
  if (!characterProgressText) {
    return;
  }
  characterProgressText.textContent = message;
}

async function startGeneration() {
  const prompt = promptInput.value.trim();
  if (!prompt) {
    setProgressMessage("请先输入一句世界初稿。");
    return;
  }
  generateBtn.disabled = true;
  importBtn.disabled = true;
  setProgress(0, 1);
  setProgressMessage("正在启动生成...");

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt }),
    });
    const data = await response.json();
    if (!data.ok) {
      throw new Error(data.error || "生成失败");
    }
    worldStageReady = false;
    pollProgress(data.job_id, data.total);
  } catch (error) {
    setProgressMessage(`生成失败：${error.message}`);
    generateBtn.disabled = false;
    importBtn.disabled = false;
  }
}

function pollProgress(jobId, total) {
  if (pollTimer) {
    clearInterval(pollTimer);
  }
  setProgress(0, total || 1);
  pollTimer = setInterval(async () => {
    try {
      const response = await fetch(`/api/progress?id=${jobId}`);
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "进度获取失败");
      }
      setProgress(data.completed, data.total);
      if (data.message) {
        setProgressMessage(data.message);
      }
      if ((data.ready || data.phase === "micro") && !worldStageReady) {
        worldStageReady = true;
        if (data.save_path) {
          latestWorldSavePath = data.save_path;
        }
        if (window.WorldView) {
          await window.WorldView.load();
        }
        showScreen("character");
      }
      if (data.status === "error") {
        clearInterval(pollTimer);
        setProgressMessage(`生成失败：${data.message}`);
        generateBtn.disabled = false;
        importBtn.disabled = false;
        return;
      }
      if (data.status === "done") {
        clearInterval(pollTimer);
        setProgress(data.total, data.total);
        setProgressMessage("生成完成，准备进入下一步。");
        if (data.save_path) {
          latestWorldSavePath = data.save_path;
        }
        if (window.WorldView) {
          await window.WorldView.load();
        }
        if (!worldStageReady) {
          showScreen("character");
        }
        generateBtn.disabled = false;
        importBtn.disabled = false;
      }
    } catch (error) {
      clearInterval(pollTimer);
      setProgressMessage(`生成中断：${error.message}`);
      generateBtn.disabled = false;
      importBtn.disabled = false;
    }
  }, 600);
}

async function importSnapshot(file) {
  if (!file) {
    return;
  }
  setProgressMessage("正在读取本地 JSON...");
  try {
    const content = await file.text();
    const response = await fetch("/api/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename: file.name, content }),
    });
    const data = await response.json();
    if (!data.ok) {
      throw new Error(data.error || "导入失败");
    }
    latestWorldSavePath = data.save_path || "";
    if (window.WorldView) {
      await window.WorldView.load();
    }
    showScreen("character");
    setProgressMessage("导入完成，准备进入下一步。");
  } catch (error) {
    setProgressMessage(`导入失败：${error.message}`);
  }
}

async function loadWorldSnapshots() {
  if (!characterWorldSelect) {
    return;
  }
  characterWorldSelect.disabled = true;
  characterWorldSelect.innerHTML = "";
  if (characterGenerateBtn) {
    characterGenerateBtn.disabled = true;
  }
  setCharacterMessage("正在读取世界存档...");
  try {
    const response = await fetch("/api/world/snapshots");
    const data = await response.json();
    if (!data.ok) {
      throw new Error(data.error || "读取失败");
    }
    const snapshots = data.snapshots || [];
    if (!snapshots.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "暂无世界存档";
      characterWorldSelect.appendChild(option);
      setCharacterMessage("暂无世界存档，请先生成世界。");
      return;
    }
    const targetName = latestWorldSavePath
      ? latestWorldSavePath.split("/").pop()
      : "";
    snapshots.forEach((snapshot, index) => {
      const option = document.createElement("option");
      option.value = snapshot.full_path || snapshot.path || "";
      option.textContent = snapshot.name || snapshot.path || `存档 ${index + 1}`;
      if (
        latestWorldSavePath
        && (option.value === latestWorldSavePath || snapshot.path === latestWorldSavePath)
      ) {
        option.selected = true;
      } else if (targetName && snapshot.name === targetName) {
        option.selected = true;
      }
      characterWorldSelect.appendChild(option);
    });
    characterWorldSelect.disabled = false;
    if (characterGenerateBtn) {
      characterGenerateBtn.disabled = false;
    }
    setCharacterMessage("请选择世界存档并开始生成角色。");
  } catch (error) {
    setCharacterMessage(`读取存档失败：${error.message}`);
  }
}

async function startCharacterGeneration() {
  if (!characterWorldSelect || !characterGenerateBtn) {
    return;
  }
  const snapshot = characterWorldSelect.value;
  const total = parseInt(characterTotalInput?.value || "0", 10);
  const pitch = characterPitchInput?.value?.trim() || "";
  if (!snapshot) {
    setCharacterMessage("请先选择世界存档。");
    return;
  }
  if (!total || total < 1) {
    setCharacterMessage("角色数量需大于 0。");
    return;
  }
  characterGenerateBtn.disabled = true;
  if (characterRefreshBtn) {
    characterRefreshBtn.disabled = true;
  }
  setCharacterProgress(0, total + 2);
  setCharacterMessage("正在启动角色生成...");

  try {
    const response = await fetch("/api/characters/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot, total, pitch }),
    });
    const data = await response.json();
    if (!data.ok) {
      throw new Error(data.error || "生成失败");
    }
    pollCharacterProgress(data.job_id, data.total);
  } catch (error) {
    setCharacterMessage(`生成失败：${error.message}`);
    characterGenerateBtn.disabled = false;
    if (characterRefreshBtn) {
      characterRefreshBtn.disabled = false;
    }
  }
}

function pollCharacterProgress(jobId, total) {
  if (characterPollTimer) {
    clearInterval(characterPollTimer);
  }
  setCharacterProgress(0, total || 1);
  characterPollTimer = setInterval(async () => {
    try {
      const response = await fetch(`/api/progress?id=${jobId}`);
      const data = await response.json();
      if (!data.ok) {
        throw new Error(data.error || "进度获取失败");
      }
      setCharacterProgress(data.completed, data.total || total || 1);
      if (data.message) {
        setCharacterMessage(data.message);
      }
      const doneTotal = data.total || total || 1;
      const isDone = data.status === "done" || (data.completed >= doneTotal && doneTotal > 0);
      if (data.status === "error") {
        clearInterval(characterPollTimer);
        setCharacterMessage(`生成失败：${data.message}`);
        characterGenerateBtn.disabled = false;
        if (characterRefreshBtn) {
          characterRefreshBtn.disabled = false;
        }
        return;
      }
      if (isDone) {
        clearInterval(characterPollTimer);
        setCharacterProgress(doneTotal, doneTotal);
        setCharacterMessage(data.message || "生成完成。");
        characterGenerateBtn.disabled = false;
        if (characterRefreshBtn) {
          characterRefreshBtn.disabled = false;
        }
        showScreen("app");
        showPage("character");
      }
    } catch (error) {
      clearInterval(characterPollTimer);
      setCharacterMessage(`生成中断：${error.message}`);
      characterGenerateBtn.disabled = false;
      if (characterRefreshBtn) {
        characterRefreshBtn.disabled = false;
      }
    }
  }, 600);
}

navButtons.forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

document.getElementById("btn-start").addEventListener("click", () => {
  showScreen("generator");
});

if (skipWorldBtn) {
  skipWorldBtn.addEventListener("click", () => {
    showScreen("character");
  });
}

generateBtn.addEventListener("click", startGeneration);

importBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) {
    importSnapshot(file);
    fileInput.value = "";
  }
});

if (characterGenerateBtn) {
  characterGenerateBtn.addEventListener("click", startCharacterGeneration);
}

if (characterRefreshBtn) {
  characterRefreshBtn.addEventListener("click", loadWorldSnapshots);
}

skipCharacterBtn.addEventListener("click", () => {
  showScreen("app");
  showPage("home");
});

if (window.WorldView) {
  window.WorldView.init({ rootId: "world-root" });
}

if (window.CharacterView) {
  window.CharacterView.init({ rootId: "character-root" });
}

showScreen("landing");
showPage("home");
