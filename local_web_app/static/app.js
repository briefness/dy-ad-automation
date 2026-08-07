const $ = (id) => document.getElementById(id);
const productFields = ["name", "type", "selling_point", "verified_claims", "ingredients", "origin", "production_process", "audience", "extra_requirements"];
const stages = ["asset_analysis", "script", "clip_selection", "audio_and_subtitles", "render", "quality"];
let preflightReady = false;
let activeJob = null;
let completedRunId = null;

function payload() {
  const product = Object.fromEntries(productFields.map((key) => [key, $(key).value]));
  return {
    asset_path: $("asset_path").value.trim(), product,
    target_duration: $("target_duration").value ? Number($("target_duration").value) : null,
    video_style: $("video_style").value, rhythm_style: $("rhythm_style").value,
    voiceover: $("voiceover").checked, voice: $("voice").value, stickers: $("stickers").value,
    output_name: $("output_name").value.trim() || null, resume: $("resume").checked,
  };
}

async function api(path, body) {
  const response = await fetch(path, { method: body ? "POST" : "GET", headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function renderEnvironment(data) {
  const checks = Object.entries(data).filter(([, value]) => value && typeof value === "object" && "ok" in value);
  const good = checks.filter(([, value]) => value.ok).length;
  $("env").textContent = good === checks.length ? "环境检查通过" : `需检查 ${checks.length - good} 项`;
  $("env").className = `status-badge ${good === checks.length ? "ready" : "warn"}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"}[character]));
}

function renderPreflight(result) {
  const coverage = result.coverage || {};
  const metrics = [[result.source_count || 0, "素材源"], [result.window_count || 0, "分析窗口"], [coverage.usable_windows || 0, "可用窗口"], [`${result.natural_main_duration || "—"}s`, "自然主时长"]];
  $("preflight-result").textContent = result.cached ? "已复用相同素材和产品事实的预检结果" : "预检完成，推荐值已写入设置";
  $("preflight-metrics").hidden = false;
  $("preflight-metrics").innerHTML = metrics.map(([value, label]) => `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`).join("");
  $("recommendation-strip").hidden = false;
  $("recommendation-copy").textContent = `建议 ${result.recommended_segments || "—"} 段 · ${result.roles?.join("、") || "按素材语义组织"}${result.warnings?.length ? ` · ${result.warnings.join("；")}` : ""}`;
  $("target_duration").value = result.recommendations?.target_duration || "";
  preflightReady = true;
  $("run").disabled = false;
}

function renderProgress(status) {
  const progress = Number(status.progress || 0);
  const isPreflight = status.kind === "preflight";
  const progressTarget = $(isPreflight ? "preflight-progress" : "progress");
  const progressBar = $(isPreflight ? "preflight-progress-bar" : "progress-bar");
  const logs = $(isPreflight ? "preflight-logs" : "logs");
  const errorBox = $(isPreflight ? "preflight-error" : "error-box");
  progressTarget.textContent = `${progress}%`;
  progressBar.style.width = `${Math.min(100, Math.max(0, progress))}%`;
  if (!isPreflight) {
    const index = stages.indexOf(status.stage);
    document.querySelectorAll("#stage-rail li").forEach((item, itemIndex) => item.classList.toggle("done", index >= 0 && itemIndex < index));
    document.querySelectorAll("#stage-rail li").forEach((item) => item.classList.toggle("active", item.dataset.stage === status.stage));
  }
  const defaultLog = isPreflight && status.stage === "completed" ? "预检完成" : isPreflight ? "等待预检" : "等待混剪";
  logs.textContent = (status.logs || []).map((entry) => entry.line).join("\n") || defaultLog;
  errorBox.hidden = !status.error;
  errorBox.textContent = status.error ? `任务未完成\n${status.error}` : "";
  const running = Boolean(status.active);
  $("cancel").disabled = !running;
  $("run").disabled = running || !preflightReady;
}

function renderResult(result) {
  if (!result?.final_path) return;
  const videoUrl = result.video_url || "";
  const links = (result.artifacts || []).map((artifact) => `<a class="artifact" href="${artifact.url}" target="_blank" rel="noreferrer" title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.label || artifact.name)}</a>`).join("");
  $("results").innerHTML = `<div class="result-card">${videoUrl ? `<video class="result-video" controls preload="metadata" src="${videoUrl}"></video>` : ""}<div class="result-details"><strong>最终成片</strong><p class="field-hint">${escapeHtml(result.final_path)}</p><div class="artifact-list">${links}</div></div></div>`;
}

function formatDate(value) { return value ? new Date(value * 1000).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" }) : ""; }

async function history() {
  const data = await api("/api/history");
  $("history").innerHTML = data.runs?.length ? data.runs.map((run) => `<div class="history-item"><div class="history-meta"><strong>${escapeHtml(run.name)}</strong><small>${formatDate(run.created_at)}</small></div><div class="history-artifacts">${(run.artifacts || []).slice(0, 5).map((artifact) => `<a class="artifact" href="${artifact.url}" target="_blank" rel="noreferrer" title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.label || artifact.name)}</a>`).join("")}</div></div>`).join("") : `<div class="empty-state">暂无最近运行</div>`;
}

async function poll() {
  try {
    const status = await api("/api/status");
    activeJob = status.active ? status.id : activeJob;
    renderProgress(status);
    if (status.kind === "preflight" && status.stage === "completed" && status.result) renderPreflight(status.result);
    if (status.kind === "run" && status.stage === "completed" && status.id !== completedRunId) {
      completedRunId = status.id;
      renderResult(status.result);
      await history();
    }
  } catch (error) { $("logs").textContent = error.message; }
}

$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    preflightReady = false; $("run").disabled = true; $("error-box").hidden = true;
    await api("/api/preflight", payload());
    await poll();
  } catch (error) { $("preflight-result").textContent = error.message; }
});

$("run").addEventListener("click", async () => {
  try {
    if (!preflightReady) throw new Error("请先完成预检");
    const status = await api("/api/jobs", payload()); activeJob = status.id; await poll();
  } catch (error) { $("error-box").hidden = false; $("error-box").textContent = error.message; }
});

$("cancel").addEventListener("click", async () => { await api("/api/cancel", {}); await poll(); });
$("refresh-history").addEventListener("click", () => history());
$("open").addEventListener("click", () => api("/api/open-output", {}));
[
  "asset_path", ...productFields,
].forEach((id) => $(id).addEventListener("input", () => { preflightReady = false; $("run").disabled = true; $("preflight-result").textContent = "输入已变化，请重新预检"; $("recommendation-strip").hidden = true; }));
window.addEventListener("pagehide", () => navigator.sendBeacon("/api/cancel", new Blob(["{}"], { type: "application/json" })));

setInterval(poll, 1200);
Promise.all([api("/api/environment").then(renderEnvironment), history(), poll()]).catch((error) => { $("env").textContent = error.message; });
