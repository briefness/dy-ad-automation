const $ = (id) => document.getElementById(id);
const productFields = ["name", "type", "selling_point", "verified_claims", "ingredients", "origin", "production_process", "audience", "extra_requirements"];
const stages = ["asset_analysis", "script", "clip_selection", "audio_and_subtitles", "render", "quality"];
const stageLabels = { asset_analysis: "正在理解素材与镜头覆盖。", script: "正在将可验证事实组织成叙事。", clip_selection: "正在选择能支撑叙事的镜头。", audio_and_subtitles: "正在处理口播、字幕与语义贴图。", render: "正在合成成片。", quality: "正在进行成片质量检查。" };
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

function setWorkbenchState(state, status) {
  document.body.dataset.state = state;
  const content = {
    idle: ["准备素材", "等待一次素材预检", "填写项目简报后预检素材"],
    preflight: ["素材能力", "这批素材可以怎样讲", "预检完成，检查推荐后开始混剪"],
    running: ["正在制作", "成片正在生成", "正在混剪，随时可以取消任务"],
    result: ["成片交付", "当前成片已就绪", "成片已完成，可查看版本或打开输出目录"],
  }[state];
  if (!content) return;
  $("stage-kicker").textContent = content[0];
  $("stage-title").textContent = content[1];
  $("dock-status").textContent = status || content[2];
  ["idle", "preflight", "running", "result"].forEach((name) => { $(`${name}-stage`).hidden = name !== state; });
}

function activateTab(tab) {
  const isCurrent = tab === "current";
  $("current-tab").setAttribute("aria-selected", String(isCurrent));
  $("history-tab").setAttribute("aria-selected", String(!isCurrent));
  $("current-view").hidden = !isCurrent;
  $("history-view").hidden = isCurrent;
  $(isCurrent ? "current-view" : "history-view").focus({ preventScroll: true });
}

function renderEnvironment(data) {
  const checks = Object.entries(data).filter(([, value]) => value && typeof value === "object" && "ok" in value);
  const good = checks.filter(([, value]) => value.ok).length;
  $("env").textContent = good === checks.length ? "环境检查通过" : `需检查 ${checks.length - good} 项`;
  $("env").className = `status-badge ${good === checks.length ? "ready" : "warn"}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
}

function renderList(target, values, className, emptyCopy) {
  $(target).innerHTML = values.length ? values.map((value) => `<span class="${className}">${escapeHtml(value)}</span>`).join("") : `<span class="empty-copy">${escapeHtml(emptyCopy)}</span>`;
}

function preflightRoles(result) {
  const planRoles = result.contract?.narrative_plan?.map((item) => item.product_story_role).filter(Boolean) || [];
  return [...new Set([...(result.roles || []), ...planRoles])];
}

function preflightWarnings(result) {
  const indexWarnings = result.index?.warnings || [];
  return [...new Set([...(result.warnings || []), ...indexWarnings])];
}

function coverageLabel(coverage) {
  const values = [coverage.usable_windows, coverage.product_windows, coverage.covered_windows].filter((value) => Number.isFinite(Number(value)));
  return values.length ? Math.max(...values) : 0;
}

function renderPreflight(result) {
  const coverage = result.coverage || result.index?.coverage || {};
  const duration = result.natural_main_duration || result.contract?.natural_main_duration || "—";
  const segments = result.recommended_segments || result.contract?.recommended_segments || "—";
  const metrics = [[result.source_count || result.index?.sources?.length || 0, "素材源"], [result.window_count || result.index?.windows?.length || 0, "分析窗口"], [coverageLabel(coverage), "可用覆盖"], [`${duration}s`, "自然主时长"]];
  const warnings = preflightWarnings(result);
  const roles = preflightRoles(result);
  $("preflight-result").textContent = result.cached ? "已复用同一批素材与产品事实的预检结果。" : "预检完成。系统已根据镜头能力写入推荐参数。";
  $("preflight-metrics").hidden = false;
  $("preflight-metrics").innerHTML = metrics.map(([value, label]) => `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`).join("");
  renderList("role-list", roles, "role-chip", "尚未识别到明确角色，请检查素材语义与产品事实。");
  $("warning-list").innerHTML = warnings.length ? warnings.map((warning) => `<li>${escapeHtml(warning)}</li>`).join("") : '<li class="no-warning">未发现需要阻断混剪的素材风险。</li>';
  $("recommendation-copy").textContent = `建议 ${segments} 段，以 ${duration} 秒自然主时长组织；${roles.length ? `优先使用${roles.join("、")}` : "按素材语义组织"}。`;
  const recommendations = result.recommendations || result.recommended_values || {};
  Object.entries(recommendations).forEach(([key, value]) => {
    const control = $(key);
    if (!control || value === null || value === undefined) return;
    if (control.type === "checkbox") control.checked = Boolean(value);
    else control.value = value;
  });
  $("inspector-hint").textContent = `已应用系统推荐：${segments} 段 / ${duration} 秒；仍可在此调整。`;
  preflightReady = true;
  $("run").disabled = false;
  setWorkbenchState("preflight");
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
    $("run-stage-copy").textContent = stageLabels[status.stage] || "正在组织镜头与叙事。";
  }
  const defaultLog = isPreflight && status.stage === "completed" ? "预检完成" : isPreflight ? "等待预检" : "等待混剪";
  logs.textContent = (status.logs || []).map((entry) => entry.line).join("\n") || defaultLog;
  errorBox.hidden = !status.error;
  errorBox.textContent = status.error ? `任务未完成\n${status.error}` : "";
  const running = Boolean(status.active);
  $("cancel").disabled = !running;
  $("run").disabled = running || !preflightReady;
  if (running) setWorkbenchState(isPreflight ? "preflight" : "running", isPreflight ? "正在预检素材能力" : "正在混剪，随时可以取消任务");
}

function renderResult(result) {
  if (!result?.final_path) return;
  const videoUrl = result.video_url || "";
  const links = (result.artifacts || []).map((artifact) => `<a class="artifact" href="${artifact.url}" target="_blank" rel="noreferrer" title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.label || artifact.name)}</a>`).join("");
  $("results").innerHTML = `<div class="result-card">${videoUrl ? `<video class="result-video" controls preload="metadata" src="${videoUrl}"></video>` : ""}<div class="result-details"><strong>最终成片</strong><p class="field-hint">${escapeHtml(result.final_path)}</p><div class="artifact-list">${links}</div></div></div>`;
  setWorkbenchState("result");
}

function formatDate(value) { return value ? new Date(value * 1000).toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" }) : ""; }

async function history() {
  const data = await api("/api/history");
  $("history").innerHTML = data.runs?.length ? data.runs.map((run) => `<div class="history-item"><div class="history-meta"><strong>${escapeHtml(run.name)}</strong><small>${formatDate(run.created_at)}</small></div><div class="history-artifacts">${(run.artifacts || []).slice(0, 5).map((artifact) => `<a class="artifact" href="${artifact.url}" target="_blank" rel="noreferrer" title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.label || artifact.name)}</a>`).join("")}</div></div>`).join("") : '<div class="empty-state">暂无最近运行</div>';
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
  } catch (error) {
    $("dock-status").textContent = error.message;
    $("logs").textContent = error.message;
  }
}

$("run-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    preflightReady = false;
    $("run").disabled = true;
    $("preflight-error").hidden = true;
    setWorkbenchState("preflight", "正在启动素材预检");
    await api("/api/preflight", payload());
    await poll();
  } catch (error) {
    $("preflight-result").textContent = error.message;
    $("preflight-error").hidden = false;
    $("preflight-error").textContent = error.message;
  }
});

$("run").addEventListener("click", async () => {
  try {
    if (!preflightReady) throw new Error("请先完成预检");
    const status = await api("/api/jobs", payload());
    activeJob = status.id;
    setWorkbenchState("running");
    await poll();
  } catch (error) {
    $("error-box").hidden = false;
    $("error-box").textContent = error.message;
    setWorkbenchState("preflight", error.message);
  }
});

$("cancel").addEventListener("click", async () => { await api("/api/cancel", {}); await poll(); });
$("refresh-history").addEventListener("click", () => history());
$("current-tab").addEventListener("click", () => activateTab("current"));
$("history-tab").addEventListener("click", () => activateTab("history"));
$("open").addEventListener("click", () => api("/api/open-output", {}));
[
  "asset_path", ...productFields,
].forEach((id) => $(id).addEventListener("input", () => {
  preflightReady = false;
  $("run").disabled = true;
  $("inspector-hint").textContent = "输入已变化，请重新预检后再开始混剪。";
  $("dock-status").textContent = "项目简报已变化，需要重新预检";
  if (document.body.dataset.state === "preflight") setWorkbenchState("idle", "项目简报已变化，需要重新预检");
}));
window.addEventListener("pagehide", () => navigator.sendBeacon("/api/cancel", new Blob(["{}"], { type: "application/json" })));

setInterval(poll, 1200);
Promise.all([api("/api/environment").then(renderEnvironment), history(), poll()]).catch((error) => { $("env").textContent = error.message; });
