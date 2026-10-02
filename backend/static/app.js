/* 实验室化学品相容性审查台 —— 前端逻辑（原生 JS） */

const $ = (id) => document.getElementById(id);

const MATERIAL_LABELS = {
  glass: "玻璃", hdpe: "HDPE", pp: "PP", ptfe: "PTFE",
  stainless_steel: "不锈钢", aluminum: "铝",
};
const EVENT_TYPE_LABELS = { transfer: "转移", mix: "混合", dispose: "废弃" };
const RULE_TYPE_LABELS = {
  acid_base: "酸碱", redox: "氧化还原", gas: "放气",
  exothermic: "放热", corrosion: "容器腐蚀", disposal: "废弃处置",
};
const STATUS_LABELS = { forbidden: "存在禁止项", review: "需人工复核", ok: "通过" };
const EVENT_STATUS_LABELS = { pending: "待执行", done: "已完成", cancelled: "已取消" };

const state = {
  zones: [], chemicals: [], events: [],
  versions: [], rules: [], reviews: [],
  currentVersionId: null,
};

/* ---------- 基础工具 ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "show" + (isError ? " error" : "");
  setTimeout(() => (t.className = ""), 2600);
}

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== null) opts.body = JSON.stringify(body);
  const res = await fetch("/api" + path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function zoneName(id) {
  const z = state.zones.find((z) => z.id === id);
  return z ? z.name : "未分区";
}
function chemName(id) {
  const c = state.chemicals.find((c) => c.id === id);
  return c ? c.name : `#${id}`;
}

/* ---------- 选项卡 ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
  });
});

/* ---------- 化学品 ---------- */
function renderChemicals() {
  const tb = document.querySelector("#chemical-table tbody");
  tb.innerHTML = state.chemicals.map((c) => `
    <tr>
      <td>${c.id}</td>
      <td>${esc(c.name)}</td>
      <td>${esc(c.cas_number)}</td>
      <td>${c.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</td>
      <td>${c.concentration_pct ?? "未知"}</td>
      <td>${c.volume_ml ?? "—"}</td>
      <td>${c.temperature_c ?? "未知"}</td>
      <td>${c.container_material ? MATERIAL_LABELS[c.container_material] || esc(c.container_material) : "未知"}</td>
      <td>${esc(zoneName(c.zone_id))}</td>
      <td>
        <button class="small" onclick="editChemical(${c.id})">编辑</button>
        <button class="small danger" onclick="delChemical(${c.id})">删除</button>
      </td>
    </tr>`).join("");
  // 同步事件表单的化学品下拉
  const opts = state.chemicals.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("ev-chem").innerHTML = opts;
  $("ev-chem2").innerHTML = `<option value="">（无）</option>` + opts;
}

function readChemicalForm() {
  const num = (id) => ($(id).value === "" ? null : Number($(id).value));
  return {
    name: $("chem-name").value.trim(),
    cas_number: $("chem-cas").value.trim(),
    tags: $("chem-tags").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
    concentration_pct: num("#chem-conc"),
    volume_ml: num("#chem-vol"),
    temperature_c: num("#chem-temp"),
    container_material: $("chem-material").value || null,
    zone_id: $("chem-zone").value ? Number($("chem-zone").value) : null,
  };
}

$("chemical-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("chem-id").value;
  try {
    if (id) await api("/chemicals/" + id, "PUT", readChemicalForm());
    else await api("/chemicals", "POST", readChemicalForm());
    toast("已保存");
    resetChemicalForm();
    await refreshAll();
  } catch (err) { toast(err.message, true); }
});

function resetChemicalForm() {
  $("chemical-form").reset();
  $("chem-id").value = "";
}
$("chem-reset").addEventListener("click", resetChemicalForm);

window.editChemical = (id) => {
  const c = state.chemicals.find((x) => x.id === id);
  if (!c) return;
  $("chem-id").value = c.id;
  $("chem-name").value = c.name;
  $("chem-cas").value = c.cas_number;
  $("chem-tags").value = c.tags.join(", ");
  $("chem-conc").value = c.concentration_pct ?? "";
  $("chem-vol").value = c.volume_ml ?? "";
  $("chem-temp").value = c.temperature_c ?? "";
  $("chem-material").value = c.container_material || "";
  $("chem-zone").value = c.zone_id || "";
  window.scrollTo({ top: 0, behavior: "smooth" });
};

window.delChemical = async (id) => {
  if (!confirm("确认删除该化学品？相关事件将一并删除。")) return;
  try {
    await api("/chemicals/" + id, "DELETE");
    toast("已删除");
    await refreshAll();
  } catch (err) { toast(err.message, true); }
};

/* ---------- 分区 ---------- */
function renderZones() {
  const tb = document.querySelector("#zone-table tbody");
  tb.innerHTML = state.zones.map((z) => {
    const count = state.chemicals.filter((c) => c.zone_id === z.id).length;
    return `<tr>
      <td>${z.id}</td><td>${esc(z.name)}</td><td>${esc(z.description)}</td><td>${count}</td>
      <td>
        <button class="small" onclick="editZone(${z.id})">编辑</button>
        <button class="small danger" onclick="delZone(${z.id})">删除</button>
      </td>
    </tr>`;
  }).join("");
  const opts = state.zones.map((z) => `<option value="${z.id}">${esc(z.name)}</option>`).join("");
  $("chem-zone").innerHTML = `<option value="">未分区</option>` + opts;
  $("ev-zone").innerHTML = opts;
}

$("zone-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = $("zone-id").value;
  const body = { name: $("zone-name").value.trim(), description: $("zone-desc").value.trim() };
  try {
    if (id) await api("/zones/" + id, "PUT", body);
    else await api("/zones", "POST", body);
    toast("已保存");
    $("zone-form").reset();
    $("zone-id").value = "";
    await refreshAll();
  } catch (err) { toast(err.message, true); }
});
$("zone-reset").addEventListener("click", () => { $("zone-form").reset(); $("zone-id").value = ""; });

window.editZone = (id) => {
  const z = state.zones.find((x) => x.id === id);
  if (!z) return;
  $("zone-id").value = z.id;
  $("zone-name").value = z.name;
  $("zone-desc").value = z.description;
};

window.delZone = async (id) => {
  if (!confirm("确认删除该分区？其中化学品将变为未分区。")) return;
  try {
    await api("/zones/" + id, "DELETE");
    toast("已删除");
    await refreshAll();
  } catch (err) { toast(err.message, true); }
};

/* ---------- 事件 ---------- */
function syncEventFormVisibility() {
  const t = $("ev-type").value;
  $("ev-chem2-wrap").style.display = t === "mix" ? "" : "none";
  $("ev-zone-wrap").style.display = t === "transfer" ? "" : "none";
}
$("ev-type").addEventListener("change", syncEventFormVisibility);

function renderEvents() {
  const tb = document.querySelector("#event-table tbody");
  tb.innerHTML = state.events.map((e) => {
    let target = "—";
    if (e.event_type === "mix") target = "与「" + esc(chemName(e.secondary_chemical_id)) + "」混合";
    else if (e.event_type === "transfer") target = "→ " + esc(zoneName(e.target_zone_id));
    return `<tr>
      <td>${e.sequence}</td>
      <td>${EVENT_TYPE_LABELS[e.event_type] || esc(e.event_type)}</td>
      <td>${esc(chemName(e.chemical_id))}</td>
      <td>${target}</td>
      <td><span class="badge ${e.status}">${EVENT_STATUS_LABELS[e.status] || e.status}</span></td>
      <td>${esc(e.note)}</td>
      <td>
        ${e.status === "pending"
          ? `<button class="small" onclick="setEventStatus(${e.id}, 'done')">完成</button>
             <button class="small" onclick="setEventStatus(${e.id}, 'cancelled')">取消</button>`
          : `<button class="small" onclick="setEventStatus(${e.id}, 'pending')">恢复待执行</button>`}
        <button class="small danger" onclick="delEvent(${e.id})">删除</button>
      </td>
    </tr>`;
  }).join("");
}

$("event-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const type = $("ev-type").value;
  const body = {
    event_type: type,
    chemical_id: Number($("ev-chem").value),
    secondary_chemical_id: type === "mix" && $("ev-chem2").value ? Number($("ev-chem2").value) : null,
    target_zone_id: type === "transfer" && $("ev-zone").value ? Number($("ev-zone").value) : null,
    sequence: Number($("ev-seq").value),
    note: $("ev-note").value.trim(),
  };
  try {
    await api("/events", "POST", body);
    toast("事件已登记");
    $("event-form").reset();
    syncEventFormVisibility();
    await refreshAll();
  } catch (err) { toast(err.message, true); }
});

window.setEventStatus = async (id, status) => {
  try {
    await api(`/events/${id}/status`, "PATCH", { status });
    await refreshAll();
  } catch (err) { toast(err.message, true); }
};

window.delEvent = async (id) => {
  if (!confirm("确认删除该事件？")) return;
  try {
    await api("/events/" + id, "DELETE");
    await refreshAll();
  } catch (err) { toast(err.message, true); }
};

/* ---------- 规则 ---------- */
function renderVersions() {
  const opts = state.versions.map((v) =>
    `<option value="${v.id}">${esc(v.name)}${v.is_active ? "（启用中）" : ""}</option>`).join("");
  $("rv-select").innerHTML = opts;
  $("review-version").innerHTML = opts;
  if (!state.currentVersionId && state.versions.length) {
    const active = state.versions.find((v) => v.is_active) || state.versions[0];
    state.currentVersionId = active.id;
  }
  if (state.currentVersionId) {
    $("rv-select").value = state.currentVersionId;
    const active = state.versions.find((v) => v.is_active);
    if (active) $("review-version").value = active.id;
  }
}

function renderRules() {
  const tb = document.querySelector("#rule-table tbody");
  tb.innerHTML = state.rules.map((r) => `<tr>
    <td>${r.id}</td>
    <td>${RULE_TYPE_LABELS[r.rule_type] || esc(r.rule_type)}</td>
    <td>${esc(r.name)}</td>
    <td><span class="badge ${r.severity}">${r.severity === "forbidden" ? "禁止" : "人工复核"}</span></td>
    <td><code>${esc(JSON.stringify(r.conditions))}</code></td>
    <td>${r.enabled ? "✔" : "✘"}</td>
    <td>
      <button class="small" onclick="editRule(${r.id})">编辑</button>
      <button class="small danger" onclick="delRule(${r.id})">删除</button>
    </td>
  </tr>`).join("");
}

async function loadRules() {
  if (!state.currentVersionId) { state.rules = []; renderRules(); return; }
  state.rules = await api(`/rule-versions/${state.currentVersionId}/rules`);
  renderRules();
}

$("rv-select").addEventListener("change", async (e) => {
  state.currentVersionId = Number(e.target.value);
  await loadRules();
});

$("rv-activate").addEventListener("click", async () => {
  try {
    await api(`/rule-versions/${$("rv-select").value}/activate`, "POST");
    toast("已启用");
    await refreshVersions();
  } catch (err) { toast(err.message, true); }
});

$("rv-copy").addEventListener("click", async () => {
  const name = $("rv-new-name").value.trim();
  if (!name) { toast("请填写新版本名", true); return; }
  try {
    const v = await api("/rule-versions", "POST", {
      name, note: "", copy_from_id: Number($("rv-select").value) || null,
    });
    toast("已创建版本 " + v.name);
    $("rv-new-name").value = "";
    state.currentVersionId = v.id;
    await refreshVersions();
    await loadRules();
  } catch (err) { toast(err.message, true); }
});

function readRuleForm() {
  let conditions = {};
  const raw = $("rule-conditions").value.trim();
  if (raw) {
    try { conditions = JSON.parse(raw); }
    catch (e) { throw new Error("条件 JSON 格式错误：" + e.message); }
  }
  return {
    rule_type: $("rule-type").value,
    name: $("rule-name").value.trim(),
    severity: $("rule-severity").value,
    conditions,
    message: $("rule-message").value.trim(),
    enabled: $("rule-enabled").checked,
  };
}

$("rule-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!state.currentVersionId) { toast("请先创建规则版本", true); return; }
  try {
    const body = readRuleForm();
    const id = $("rule-id").value;
    if (id) await api("/rules/" + id, "PUT", body);
    else await api(`/rule-versions/${state.currentVersionId}/rules`, "POST", body);
    toast("规则已保存");
    resetRuleForm();
    await loadRules();
  } catch (err) { toast(err.message, true); }
});

function resetRuleForm() {
  $("rule-form").reset();
  $("rule-id").value = "";
  $("rule-enabled").checked = true;
}
$("rule-reset").addEventListener("click", resetRuleForm);

window.editRule = (id) => {
  const r = state.rules.find((x) => x.id === id);
  if (!r) return;
  $("rule-id").value = r.id;
  $("rule-type").value = r.rule_type;
  $("rule-name").value = r.name;
  $("rule-severity").value = r.severity;
  $("rule-conditions").value = JSON.stringify(r.conditions, null, 2);
  $("rule-message").value = r.message;
  $("rule-enabled").checked = r.enabled;
  window.scrollTo({ top: 0, behavior: "smooth" });
};

window.delRule = async (id) => {
  if (!confirm("确认删除该规则？")) return;
  try {
    await api("/rules/" + id, "DELETE");
    await loadRules();
  } catch (err) { toast(err.message, true); }
};

/* ---------- 审查 ---------- */
function renderStepZones(before, after) {
  // 前后快照均含全部分区；只列出任一时刻非空的分区，变化的分区高亮对比
  const bMap = {};
  before.forEach((z) => (bMap[String(z.zone_id)] = z));
  return after.map((a) => {
    const b = bMap[String(a.zone_id)] || { chemicals: [] };
    if (!b.chemicals.length && !a.chemicals.length) return "";
    const bIds = new Set(b.chemicals.map((c) => c.id));
    const aIds = new Set(a.chemicals.map((c) => c.id));
    const changed = b.chemicals.some((c) => !aIds.has(c.id))
      || a.chemicals.some((c) => !bIds.has(c.id));
    const names = (chems, otherIds, cls) =>
      chems.map((c) => (otherIds.has(c.id)
        ? esc(c.name)
        : `<span class="${cls}">${esc(c.name)}</span>`)).join("、") || "（空）";
    if (!changed) {
      return `<div class="zone-row"><b>${esc(a.zone_name)}</b>：${names(a.chemicals, bIds, "")}</div>`;
    }
    return `<div class="zone-row changed"><b>${esc(a.zone_name)}</b>：
      ${names(b.chemicals, aIds, "removed")} → ${names(a.chemicals, bIds, "added")}</div>`;
  }).join("");
}

function renderEventSteps(steps) {
  if (!steps || !steps.length) return "";
  const items = steps.map((s) => {
    const ev = s.event;
    let title;
    if (ev.event_type === "transfer") {
      title = `「${esc(ev.chemical_name)}」→ ${esc(ev.target_zone_name || "未分区")}`;
    } else if (ev.event_type === "mix") {
      title = `「${esc(ev.chemical_name)}」与「${esc(ev.secondary_chemical_name || "?")}」混合`;
    } else {
      title = `废弃「${esc(ev.chemical_name)}」`;
    }
    const body = s.skipped
      ? `<div class="hint">${esc(s.skipped)}</div>`
      : `<div class="zone-diff">${renderStepZones(s.before, s.after)}</div>`;
    return `<div class="event-step">
      <div class="event-step-head">
        <span class="badge pending">#${ev.sequence} ${esc(ev.event_type_label)}</span>
        ${title}
        ${ev.note ? `<span class="hint">（${esc(ev.note)}）</span>` : ""}
        <span class="hint">新增发现 ${s.finding_count} 条</span>
      </div>
      ${body}
    </div>`;
  }).join("");
  return `<div class="card"><strong>事件模拟过程（按执行顺序，库存状态逐步更新）：</strong>
    ${items}</div>`;
}

function renderReviewResult(data) {
  const r = data.result;
  const box = $("review-result");
  const bannerText = {
    forbidden: "⛔ 审查结论：存在禁止项，相关操作不得执行",
    review: "⚠️ 审查结论：存在数据缺失或需人工复核项",
    ok: "✅ 审查结论：未发现冲突",
  }[r.status];

  let html = `<div class="banner ${r.status}">
    <strong>${bannerText}</strong><br>
    规则版本：${esc(data.rule_version_name)} ｜ 禁止项 ${r.summary.forbidden_count} 条 ｜
    待复核 ${r.summary.review_count} 条 ｜ 化学品 ${r.summary.chemical_count} 种 ｜
    待执行事件 ${r.summary.pending_event_count} 个
  </div>`;

  if (r.earliest_conflict) {
    const ec = r.earliest_conflict;
    html += `<div class="card"><strong>最早冲突：</strong>${esc(ec.when)} —
      「${esc(ec.objects.join("」与「"))}」在 ${esc(ec.zone_name)} 触发规则「${esc(ec.rule_name)}」</div>`;
  }
  if (r.isolation.length) {
    html += `<div class="card"><strong>需隔离对象：</strong><ul>
      ${r.isolation.map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>`;
  }

  html += renderEventSteps(r.event_steps);  // 旧记录无此字段时自动跳过

  if (!r.findings.length) {
    html += `<div class="card">所有同区组合与事件均通过检查。</div>`;
  } else {
    html += r.findings.map((f) => {
      const where = f.event
        ? `事件 #${f.event.sequence}（${esc(f.event.event_type_label)}）后 ｜ ${esc(f.zone_name)}`
        : `当前储存 ｜ ${esc(f.zone_name)}`;
      return `<div class="finding ${f.severity}">
        <h4>
          <span class="badge ${f.severity}">${f.severity === "forbidden" ? "禁止" : "复核"}</span>
          【${esc(f.rule_type_label)}】${esc(f.rule_name)}
        </h4>
        <div class="meta">${where} ｜ 涉及：${f.objects.map((o) =>
          `「${esc(o.name)}${o.cas_number ? " (" + esc(o.cas_number) + ")" : ""}」`).join("、")}</div>
        ${f.message ? `<div>${esc(f.message)}</div>` : ""}
        <ul>${f.conditions.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
      </div>`;
    }).join("");
  }
  box.innerHTML = html;
}

$("run-review").addEventListener("click", async () => {
  try {
    const versionId = $("review-version").value;
    const data = await api("/reviews", "POST",
      versionId ? { rule_version_id: Number(versionId) } : {});
    renderReviewResult(data);
    await refreshReviews();
    toast("审查完成");
  } catch (err) { toast(err.message, true); }
});

function renderReviews() {
  const tb = document.querySelector("#review-table tbody");
  tb.innerHTML = state.reviews.map((r) => `<tr>
    <td>${r.id}</td>
    <td>${esc(r.rule_version_name)}</td>
    <td><span class="badge ${r.status}">${STATUS_LABELS[r.status] || r.status}</span></td>
    <td>${esc(r.created_at)}</td>
    <td><button class="small" onclick="viewReview(${r.id})">查看</button></td>
  </tr>`).join("");
}

window.viewReview = async (id) => {
  try {
    const data = await api("/reviews/" + id);
    renderReviewResult(data);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) { toast(err.message, true); }
};

/* ---------- 数据加载 ---------- */
async function refreshVersions() {
  state.versions = await api("/rule-versions");
  renderVersions();
}
async function refreshReviews() {
  state.reviews = await api("/reviews");
  renderReviews();
}
async function refreshAll() {
  [state.zones, state.chemicals, state.events] = await Promise.all([
    api("/zones"), api("/chemicals"), api("/events"),
  ]);
  renderZones();
  renderChemicals();
  renderEvents();
}

(async function init() {
  try {
    await refreshAll();
    await refreshVersions();
    await loadRules();
    await refreshReviews();
    syncEventFormVisibility();
  } catch (err) {
    toast("初始化失败：" + err.message, true);
  }
})();
