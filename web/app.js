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
const skipCharacterBtn = document.getElementById("btn-skip-character");

let pollTimer = null;

function showScreen(name) {
  Object.entries(screens).forEach(([key, node]) => {
    node.classList.toggle("active", key === name);
  });
  nav.classList.toggle("is-hidden", name !== "app");
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
}

function setProgress(completed, total) {
  const percent = total > 0 ? Math.round((completed / total) * 100) : 0;
  progressBar.style.width = `${percent}%`;
  progressText.textContent = `进度 ${completed}/${total} (${percent}%)`;
}

function setProgressMessage(message) {
  progressText.textContent = message;
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
        if (window.WorldView) {
          await window.WorldView.load();
        }
        showScreen("character");
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
    if (window.WorldView) {
      await window.WorldView.load();
    }
    showScreen("character");
    setProgressMessage("导入完成，准备进入下一步。");
  } catch (error) {
    setProgressMessage(`导入失败：${error.message}`);
  }
}

navButtons.forEach((btn) => {
  btn.addEventListener("click", () => showPage(btn.dataset.page));
});

document.getElementById("btn-start").addEventListener("click", () => {
  showScreen("generator");
});

generateBtn.addEventListener("click", startGeneration);

importBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (file) {
    importSnapshot(file);
    fileInput.value = "";
  }
});

skipCharacterBtn.addEventListener("click", () => {
  showScreen("app");
  showPage("home");
});

if (window.WorldView) {
  window.WorldView.init({ rootId: "world-root" });
}

showScreen("landing");
showPage("home");
