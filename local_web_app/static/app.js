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
    result: ["成片交付", "当前成片已就绪", "成片已完成，可审阅并记录真实反馈"],
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

function focusField(fieldId) {
  const el = $(fieldId);
  if (!el) return;
  const details = el.closest("details");
  if (details) details.open = true;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.focus({ preventScroll: true });
}

function renderErrorBox(targetEl, errorText, diagnostics) {
  if (!errorText) {
    targetEl.hidden = true;
    targetEl.innerHTML = "";
    return;
  }
  targetEl.hidden = false;
  if (!diagnostics || !diagnostics.summary) {
    targetEl.textContent = `任务未完成\n${errorText}`;
    return;
  }
  const actionsHtml = (diagnostics.actions || []).map((act) => (
    `<div class="diag-action"><button type="button" class="diag-jump" data-field="${escapeHtml(act.field || "")}">${escapeHtml(act.label || "去调整")}</button><span>${escapeHtml(act.hint || "")}</span></div>`
  )).join("");
  targetEl.innerHTML = `<div class="diag-title">已按证据/质量红线阻断输出</div><p class="diag-summary">${escapeHtml(diagnostics.summary)}</p>${actionsHtml ? `<div class="diag-actions">${actionsHtml}</div>` : ""}<details class="diag-raw"><summary>原始阻断信息</summary><pre>${escapeHtml(errorText)}</pre></details>`;
  targetEl.querySelectorAll(".diag-jump").forEach((btn) => {
    btn.addEventListener("click", () => focusField(btn.dataset.field));
  });
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
  renderErrorBox(errorBox, status.error, status.diagnostics);
  const running = Boolean(status.active);
  $("cancel").disabled = !running;
  $("run").disabled = running || !preflightReady;
  if (running) setWorkbenchState(isPreflight ? "preflight" : "running", isPreflight ? "正在预检素材能力" : "正在混剪，随时可以取消任务");
}

function renderRuleChips(rules, targetKind) {
  if (!rules || !rules.length) return '<span class="empty-copy">冷启动为空：仅由你在不同成片上的明确反馈逐步积累。</span>';
  return rules.map((rule) => {
    const isActive = rule.status === "active";
    const badge = isActive ? "已生效" : `试用 ${rule.distinct_video_count}/2`;
    return `<button type="button" class="rule-pill ${isActive ? "active" : "provisional"}" data-target="${escapeHtml(targetKind)}" data-rule="${escapeHtml(rule.text)}"><span>${escapeHtml(rule.text)}</span><small>${badge}</small></button>`;
  }).join("");
}

async function refreshFeedbackPolicy() {
  const container = $("feedback-policy-list");
  if (!container) return;
  try {
    const category = encodeURIComponent($("type")?.value?.trim() || "");
    const style = encodeURIComponent($("video_style")?.value || "");
    const policy = await api(`/api/feedback/policy?category=${category}&style=${style}`);
    container.innerHTML = `<div class="policy-group"><span class="field-note">脚本规则</span><div class="rule-pill-list">${renderRuleChips(policy.script_rules, "script")}</div></div><div class="policy-group"><span class="field-note">贴图规则</span><div class="rule-pill-list">${renderRuleChips(policy.sticker_rules, "sticker")}</div></div>`;
    container.querySelectorAll(".rule-pill").forEach((btn) => {
      btn.addEventListener("click", () => {
        if ($("fb-target")) $("fb-target").value = btn.dataset.target || "script";
        if ($("fb-rule")) $("fb-rule").value = btn.dataset.rule || "";
        toggleStickerPicker();
      });
    });
  } catch (err) {
    container.innerHTML = `<span class="empty-copy">规则读取失败：${escapeHtml(err.message)}</span>`;
  }
}

function toggleStickerPicker() {
  const wrap = $("fb-sticker-wrap");
  if (!wrap || !$("fb-target")) return;
  wrap.hidden = $("fb-target").value !== "sticker";
}

function renderResult(result) {
  if (!result?.final_path) return;
  const videoUrl = result.video_url || "";
  const videoRel = result.video_relative_path || "";
  const stickers = result.stickers || [];
  const links = (result.artifacts || []).map((artifact) => `<a class="artifact" href="${artifact.url}" target="_blank" rel="noreferrer" title="${escapeHtml(artifact.name)}">${escapeHtml(artifact.label || artifact.name)}</a>`).join("");
  const stickerOptions = stickers.length
    ? stickers.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.text)}（${escapeHtml(item.kind || "sticker")}）</option>`).join("")
    : '<option value="">本次成片无渲染贴图</option>';
  const feedbackPanel = videoRel ? `<section class="feedback-panel" aria-labelledby="fb-title"><div class="section-kicker">真实反馈学习闭环</div><h3 id="fb-title">审阅本条成片并记录偏好规则</h3><p class="field-hint">仅人工明确反馈可创建规则；同一规则在 2 条不同成片上确认后自动晋升为已生效规则。</p><div class="feedback-grid"><label>反馈对象<select id="fb-target"><option value="script">脚本 / 字幕文案</option><option value="sticker">语义贴图</option></select></label><label>判断结论<select id="fb-verdict"><option value="violated">违反规则（下次避免）</option><option value="satisfied">符合预期（下次保留）</option></select></label><label id="fb-sticker-wrap" hidden>对应贴图<select id="fb-sticker-id">${stickerOptions}</select></label></div><label>规则描述<input id="fb-rule" placeholder="例如：字幕应该像带货文案而不是画面描述 / 产地贴图不要遮挡主体"></label><label>具体批注 <span class="field-note">可选</span><input id="fb-comment" placeholder="记录这版哪里好或哪里不对"></label><div class="feedback-actions"><button id="fb-submit" class="button primary" type="button">记录真实反馈</button><span id="fb-status" class="field-hint"></span></div><div id="feedback-policy-list" class="policy-box">正在载入当前偏好规则…</div></section>` : "";
  $("results").innerHTML = `<div class="result-card">${videoUrl ? `<video class="result-video" controls preload="metadata" src="${videoUrl}"></video>` : ""}<div class="result-details"><strong>最终成片</strong><p class="field-hint">${escapeHtml(result.final_path)}</p><div class="artifact-list">${links}</div></div></div>${feedbackPanel}`;
  if (videoRel) {
    $("fb-target").addEventListener("change", toggleStickerPicker);
    $("fb-submit").addEventListener("click", async () => {
      const ruleText = $("fb-rule").value.trim();
      if (!ruleText) { $("fb-status").textContent = "请先填写规则描述"; return; }
      try {
        $("fb-status").textContent = "正在写入反馈…";
        const resp = await api("/api/feedback", {
          target: $("fb-target").value,
          verdict: $("fb-verdict").value,
          rule_text: ruleText,
          comment: $("fb-comment").value.trim(),
          video_path: videoRel,
          sticker_id: $("fb-sticker-id")?.value || "",
          product_category: $("type")?.value?.trim() || "",
          video_style: $("video_style")?.value || "",
        });
        $("fb-status").textContent = resp.recorded ? "已记录到用户偏好库（点击已有规则可跨成片巩固）" : "相同反馈已存在，无需重复记录";
        $("fb-comment").value = "";
        await refreshFeedbackPolicy();
      } catch (err) {
        $("fb-status").textContent = `记录失败：${err.message}`;
      }
    });
    refreshFeedbackPolicy();
  }
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
    renderErrorBox($("preflight-error"), error.message, null);
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
    renderErrorBox($("error-box"), error.message, null);
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
