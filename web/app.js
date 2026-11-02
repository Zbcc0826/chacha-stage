/* 虚拟主播 / 桌宠引擎。
 * 滑杆范围直接读当前 moc。拖动即锁定，并在每帧绘制前写回。
 * 模型列表来自 /api/models。模型自带动作由 engine.js 播放。
 */

/** 一键姿态。模型里没有的参数会跳过，数值按真实范围夹紧。 */
const PRESETS = [
  ["默认", null],
  ["微笑", { ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamMouthForm: 1, ParamBrowLY: 0.35, ParamBrowRY: 0.35 }],
  ["害羞", { Param2: 1, ParamCheek: 1, ParamEyeLSmile: 0.7, ParamEyeRSmile: 0.7, ParamMouthForm: 0.45, ParamAngleZ: 10, ParamEyeBallY: -0.45 }],
  ["生气", { ParamBrowLY: -1, ParamBrowRY: -1, ParamBrowLAngle: -1, ParamBrowRAngle: -1, ParamMouthForm: -1, ParamEyeLOpen: 0.75, ParamEyeROpen: 0.75 }],
  ["惊讶", { ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamBrowLY: 1, ParamBrowRY: 1, ParamMouthOpenY: 0.9 }],
  ["难过", { ParamMouthForm: -0.6, ParamBrowLForm: -1, ParamBrowRForm: -1, ParamBrowLY: -0.4, ParamBrowRY: -0.4, ParamAngleY: -12, ParamEyeBallY: -0.6 }],
  ["星星眼", { Param: 1, ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamMouthForm: 0.6 }],
  ["脸红", { Param2: 1 }],
  ["旋风呆毛", { Param36: 1 }],
  ["歪头", { ParamAngleZ: 25, ParamAngleX: 12 }],
  ["侧身", { ParamBodyAngleX: 10, ParamBodyAngleZ: 8, ParamAngleX: 12, ParamAngleZ: 8 }],
];

const locks = new Map();
const lastDrawn = new Map();
const byId = new Map();
let params = [];
let core = null;
let model = null;
let app = null;
let physicsRef = null;
let blinkRef = null;
let ahoge = null;
let resetBase = false;
let zoom = 1;
let panX = 0;
let panY = 0;
let draggingStage = false;
let dragLast = null;
let draggingIndex = -1;
let frames = 0;
let fpsStamp = performance.now();
let fps = 0;
let catalog = [];
let currentModel = null;
let loadToken = 0;

const $ = (id) => document.getElementById(id);

/** 把 Cubism 参数 ID 统一成字符串。 */
function asId(value) {
  if (typeof value === "string") return value;
  if (value && typeof value.s === "string") return value.s;
  return String(value);
}

function fmt(value, min, max) {
  const span = Math.abs(max - min);
  const digits = span >= 100 ? 1 : span >= 10 ? 2 : 3;
  return Number(value).toFixed(digits);
}

function showError(error) {
  const box = $("error");
  box.hidden = false;
  box.textContent = error && error.message ? error.message : String(error);
  $("status").textContent = "模型加载失败";
}

function toast(text) {
  const box = $("toast");
  box.hidden = false;
  box.textContent = text;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => {
    box.hidden = true;
  }, 2200);
}

async function copyText(text) {
  await navigator.clipboard.writeText(text);
  toast("已复制 " + text.length + " 字");
}

/** 读取 cdi3 里的中文名和分组，失败时仍可用原始 ID。 */
async function loadLabels(cdiUrl) {
  const names = new Map();
  const groups = new Map();
  const order = [];
  try {
    const data = await (await fetch(cdiUrl)).json();
    for (const group of data.ParameterGroups || []) {
      const name = (group.Name || "").trim();
      groups.set(group.Id, name || "基础");
      order.push(group.Id);
    }
    for (const item of data.Parameters || []) {
      names.set(item.Id, {
        name: item.Name || item.Id,
        groupId: item.GroupId || "",
      });
    }
  } catch (error) {
    console.warn("cdi3 读取失败", error);
  }
  return { names, groups, order };
}

/** 用模型画布把角色摆进舞台，滚轮和拖拽在这个结果上叠加。 */
function layoutModel() {
  if (!model || !app) return;
  const viewW = app.screen.width;
  const viewH = app.screen.height;
  const iw = model.internalModel.originalWidth || model.internalModel.width || 1;
  const ih = model.internalModel.originalHeight || model.internalModel.height || 1;
  const fit = Math.min(viewW / iw, viewH / ih) * 0.9;
  model.scale.set(fit * zoom);
  if (model.anchor) model.anchor.set(0.5, 0.62);
  model.position.set(viewW * 0.5 + panX, viewH * 0.56 + panY);
}

function bindStagePointer() {
  const stage = $("stage");
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    const next = zoom * (event.deltaY > 0 ? 0.92 : 1.08);
    zoom = Math.min(4, Math.max(0.25, next));
    layoutModel();
  }, { passive: false });
  stage.addEventListener("pointerdown", (event) => {
    if ($("opt-mouse").checked || event.button !== 0) return;
    draggingStage = true;
    dragLast = { x: event.clientX, y: event.clientY };
    stage.setPointerCapture(event.pointerId);
  });
  stage.addEventListener("pointermove", (event) => {
    if ($("opt-mouse").checked && model) {
      const rect = app.view.getBoundingClientRect();
      model.focus(event.clientX - rect.left, event.clientY - rect.top);
      return;
    }
    if (!draggingStage || !dragLast) return;
    panX += event.clientX - dragLast.x;
    panY += event.clientY - dragLast.y;
    dragLast = { x: event.clientX, y: event.clientY };
    layoutModel();
  });
  const endDrag = () => {
    draggingStage = false;
    dragLast = null;
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
}

/** 物理和眨眼是运行时对象。官方呼吸会顺带摆头，所以改由试验台自己写 ParamBreath。 */
function bindToggles() {
  $("opt-physics").addEventListener("change", () => {
    model.internalModel.physics = $("opt-physics").checked ? physicsRef : null;
  });
  $("opt-blink").addEventListener("change", () => {
    model.internalModel.eyeBlink = $("opt-blink").checked ? blinkRef : null;
  });
  $("opt-mouse").addEventListener("change", () => {
    if (!$("opt-mouse").checked && model) {
      model.internalModel.focusController.focus(0, 0, true);
    }
  });
  $("search").addEventListener("input", applyFilter);
  $("only-locked").addEventListener("change", applyFilter);
  $("btn-reset").addEventListener("click", resetAll);
  $("btn-copy").addEventListener("click", () => copyText(poseJson()));
  $("btn-catalog").addEventListener("click", () => copyText(catalogText()));
}

function applyRuntimeToggles() {
  model.internalModel.physics = $("opt-physics").checked ? physicsRef : null;
  // SDK 的 breath 会同时改头角和身体，试验参数时会把滑杆带跑。
  model.internalModel.breath = null;
  model.internalModel.eyeBlink = $("opt-blink").checked ? blinkRef : null;
}

/** 未锁定的待机动作。锁定值在这之后写入，因此滑杆始终优先。 */
function applyIdle(now) {
  const t = now / 1000;
  if ($("opt-breath").checked) {
    const breath = byId.get("ParamBreath");
    if (breath && !locks.has(breath.index)) {
      const wave = (Math.sin((t * Math.PI * 2) / 3.2) + 1) / 2;
      core.setParameterValueByIndex(breath.index, wave);
    }
  }
  if ($("opt-sway") && $("opt-sway").checked) {
    const sway = [
      ["ParamAngleX", 6, 6.5],
      ["ParamAngleY", 3, 3.5],
      ["ParamAngleZ", 4, 5.5],
      ["ParamBodyAngleX", 8, 5.2],
      ["ParamBodyAngleZ", 5, 6.2],
    ];
    for (const [id, amp, cycle] of sway) {
      const item = byId.get(id);
      if (!item || locks.has(item.index)) continue;
      const wave = Math.sin((t * Math.PI * 2) / cycle) * amp;
      core.setParameterValueByIndex(item.index, wave);
    }
  }
}

/** 绘制前写回锁定值。保存参数发生在这之前，所以这里的写入只影响当前这一帧的画面。 */
function onBeforeDraw() {
  const now = performance.now();
  frames += 1;
  if (now - fpsStamp > 500) {
    fps = Math.round((frames * 1000) / (now - fpsStamp));
    frames = 0;
    fpsStamp = now;
  }
  if (!core) return;
  applyIdle(now);
  if ($("opt-ahoge").checked && ahoge && !locks.has(ahoge.index)) {
    const span = ahoge.max - ahoge.min;
    if (span > 1) {
      const t = (now / 1000) % 0.983;
      core.setParameterValueByIndex(ahoge.index, ahoge.min + (t / 0.983) * span);
    }
  }
  for (const [index, value] of locks) {
    core.setParameterValueByIndex(index, value);
  }
  applyPerformance(now);
  for (const item of params) {
    lastDrawn.set(item.index, core.getParameterValueByIndex(item.index));
  }
}

/** 恢复默认必须发生在 saveParameters 之前，否则下一帧又会加载旧底值。 */
function onBeforeMotion() {
  if (!resetBase) return;
  resetBase = false;
  for (const item of params) {
    core.setParameterValueByIndex(item.index, item.def);
  }
}

function hookModel() {
  const internal = model.internalModel;
  core = internal.coreModel;
  physicsRef = internal.physics || null;
  blinkRef = internal.eyeBlink || null;
  applyRuntimeToggles();
  internal.on("beforeMotionUpdate", onBeforeMotion);
  internal.on("beforeModelUpdate", onBeforeDraw);
}

function buildParams(labels) {
  const count = core.getParameterCount();
  const rows = [];
  for (let index = 0; index < count; index += 1) {
    const id = asId(core._parameterIds[index]);
    const meta = labels.names.get(id) || { name: id, groupId: "基础" };
    const item = {
      index,
      id,
      name: meta.name,
      groupId: meta.groupId || "基础",
      min: core.getParameterMinimumValue(index),
      max: core.getParameterMaximumValue(index),
      def: core.getParameterDefaultValue(index),
    };
    item.value = item.def;
    rows.push(item);
    byId.set(id, item);
    if (id === "Param38") ahoge = item;
  }
  params = rows;
  return rows;
}

function buildPresetButtons() {
  const bar = $("presets");
  for (const [name, pose] of PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = name;
    button.addEventListener("click", () => applyPreset(name, pose, button));
    bar.appendChild(button);
  }
}

/** 这些参数是表情和身体，不是头发裙子的物理输出。 */
function isFaceParam(id) {
  return ["ParamAngle", "ParamEye", "ParamMouth", "ParamBrow", "ParamBody", "ParamBreath", "ParamCheek"].some((prefix) => id.startsWith(prefix));
}

function buildGroups(labels) {
  const host = $("groups");
  host.innerHTML = "";
  const grouped = new Map();
  for (const item of params) {
    if (!grouped.has(item.groupId)) grouped.set(item.groupId, []);
    grouped.get(item.groupId).push(item);
  }
  const groupIds = [...labels.order.filter((id) => grouped.has(id))];
  for (const id of grouped.keys()) {
    if (!groupIds.includes(id)) groupIds.push(id);
  }
  for (const groupId of groupIds) {
    const items = grouped.get(groupId);
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const title = (labels.groups.get(groupId) || "").trim() || (groupId === "基础" ? "基础" : groupId);
    // 脸和身体直接打开；头发裙子这类物理输出默认收起，避免把表情滑杆顶出屏幕。
    details.open = items.some((item) => isFaceParam(item.id)) || title === "按键" || title === "呆毛";
    summary.innerHTML = title + ' <span class="count">' + items.length + "</span>";
    const wrap = document.createElement("div");
    wrap.className = "rows";
    for (const item of items) wrap.appendChild(buildRow(item));
    details.append(summary, wrap);
    details.dataset.group = groupId;
    host.appendChild(details);
  }
}

function buildRow(item) {
  const row = document.createElement("label");
  row.className = "param";
  row.dataset.id = item.id;
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = item.name;
  const id = document.createElement("div");
  id.className = "id";
  id.textContent = item.id + "  " + fmt(item.min, item.min, item.max) + " ~ " + fmt(item.max, item.min, item.max);
  const num = document.createElement("div");
  num.className = "num";
  num.textContent = fmt(item.def, item.min, item.max);
  const slider = document.createElement("input");
  slider.type = "range";
  slider.min = String(item.min);
  slider.max = String(item.max);
  slider.step = String(stepOf(item.min, item.max));
  slider.value = String(item.def);
  slider.addEventListener("pointerdown", () => {
    draggingIndex = item.index;
  });
  slider.addEventListener("input", () => {
    const value = clamp(item, Number(slider.value));
    locks.set(item.index, value);
    num.textContent = fmt(value, item.min, item.max);
    row.classList.add("locked");
    markPreset("");
    $("status").textContent = "锁定 " + item.name + " = " + fmt(value, item.min, item.max);
  });
  slider.addEventListener("dblclick", (event) => {
    event.preventDefault();
    unlock(item.id);
  });
  row.append(name, num, id, slider);
  item.row = row;
  item.slider = slider;
  item.valueEl = num;
  return row;
}

function stepOf(min, max) {
  const span = Math.abs(max - min);
  if (span >= 100) return 0.1;
  if (span >= 10) return 0.01;
  return 0.001;
}

function clamp(item, value) {
  return Math.min(item.max, Math.max(item.min, value));
}

function unlock(id) {
  const item = byId.get(id);
  if (!item) return;
  locks.delete(item.index);
  item.row.classList.remove("locked");
  applyFilter();
}

function resetAll() {
  stopSpeech();
  locks.clear();
  resetBase = true;
  markPreset("默认");
  for (const item of params) item.row.classList.remove("locked");
  $("status").textContent = "已恢复默认姿态";
  applyFilter();
}

function applyPreset(name, pose, button) {
  locks.clear();
  for (const item of params) item.row.classList.remove("locked");
  if (!pose) {
    resetAll();
    markPreset(name);
    return;
  }
  const applied = [];
  for (const [id, raw] of Object.entries(pose)) {
    const item = byId.get(id);
    if (!item) continue;
    const value = clamp(item, raw);
    locks.set(item.index, value);
    item.row.classList.add("locked");
    applied.push(item.name);
  }
  markPreset(name);
  $("status").textContent = name + " · " + applied.join("、");
  button.blur();
}

function markPreset(name) {
  for (const button of $("presets").querySelectorAll("button")) {
    button.classList.toggle("active", button.textContent === name);
  }
}

function applyFilter() {
  const query = $("search").value.trim().toLowerCase();
  const only = $("only-locked").checked;
  for (const item of params) {
    const text = (item.name + " " + item.id).toLowerCase();
    const visible = (!query || text.includes(query)) && (!only || locks.has(item.index));
    item.row.classList.toggle("hidden", !visible);
  }
}

function syncSliders() {
  if (!core) return;
  let changed = 0;
  for (const item of params) {
    if (item.index === draggingIndex) continue;
    const value = locks.has(item.index) ? locks.get(item.index) : lastDrawn.get(item.index);
    if (value === undefined) continue;
    item.valueEl.textContent = fmt(value, item.min, item.max);
    const shown = Number(item.slider.value);
    if (Math.abs(shown - value) > stepOf(item.min, item.max) * 0.5) {
      item.slider.value = String(value);
    }
    const away = Math.abs(value - item.def) > Math.max(0.02, Math.abs(item.max - item.min) * 0.015);
    item.row.classList.toggle("hot", away);
    if (away) changed += 1;
  }
  const base = fps + " fps · 参数 " + params.length + " · 锁定 " + locks.size + " · 偏离 " + changed;
  // 合成失败的原因留在状态栏，直到下一次开口。
  if (speech.notice) $("status").textContent = speech.notice;
  else if (!locks.size && !speech.busy && !speech.active) $("status").textContent = base;
}

function poseJson() {
  const pose = {};
  for (const item of params) {
    if (!locks.has(item.index)) continue;
    pose[item.id] = Number(fmt(locks.get(item.index), item.min, item.max));
  }
  return JSON.stringify(pose, null, 2);
}

function catalogText() {
  const lines = ["id\tname\tgroup\tmin\tmax\tdefault"];
  for (const item of params) {
    lines.push([item.id, item.name, item.groupId, item.min, item.max, item.def].join("\t"));
  }
  return lines.join("\n");
}

function setParam(id, value) {
  const item = byId.get(id);
  if (!item) throw new Error("没有参数 " + id);
  const next = clamp(item, Number(value));
  locks.set(item.index, next);
  item.row.classList.add("locked");
  item.slider.value = String(next);
  item.valueEl.textContent = fmt(next, item.min, item.max);
  return { id, name: item.name, value: next, min: item.min, max: item.max, def: item.def };
}

async function loadStatus() {
  try {
    const status = await (await fetch("/api/status")).json();
    const pill = $("llm");
    pill.textContent = status.llmReady ? "dsh 已连接" : "dsh 未配置密钥";
    pill.classList.toggle("ready", !!status.llmReady);
    if (status.vibe) adoptVibe(status.vibe);
    showDanmaku(status.danmaku);
  } catch (error) {
    $("llm").textContent = "状态未知";
  }
}

function clearPoseState() {
  locks.clear();
  lastDrawn.clear();
  byId.clear();
  params = [];
  ahoge = null;
  resetBase = false;
  draggingIndex = -1;
}

function releaseModel() {
  if (!model) return;
  const old = model;
  model = null;
  core = null;
  try {
    old.autoUpdate = false;
  } catch (error) {
    console.warn(error);
  }
  app.stage.removeChild(old);
  old.destroy();
}

/** 换成另一套模型，并按它自己的 cdi 重画参数表。 */
async function showModel(modelId) {
  stopSpeech();
  const info = catalog.find((item) => item.id === modelId);
  if (!info || !info.ready) throw new Error("模型不可用");
  const token = ++loadToken;
  currentModel = info;
  $("model-pick").value = info.id;
  updateBrand(info);
  $("status").textContent = "正在加载 " + info.name + "…";
  $("error").hidden = true;
  const base = "/models/" + encodeURIComponent(info.id) + "/";
  const labels = await loadLabels(base + info.cdi);
  const next = await PIXI.live2d.Live2DModel.from(base + info.model, {
    autoInteract: false,
    autoUpdate: true,
  });
  if (token !== loadToken) {
    next.destroy();
    return;
  }
  releaseModel();
  clearPoseState();
  zoom = 1;
  panX = 0;
  panY = 0;
  model = next;
  app.stage.addChild(model);
  hookModel();
  buildParams(labels);
  buildGroups(labels);
  layoutModel();
  window.stageModel = model;
  model.eventMode = "static";
  window.dispatchEvent(new CustomEvent("stage-model"));
  $("ahoge-toggle").hidden = !ahoge;
  refreshPresetVisibility();
  $("status").textContent = info.name + " 已加载 · 参数 " + params.length;
}

/** 这套模型一个都对不上的预设不显示，例如 areazil 没有旋风呆毛。 */
function refreshPresetVisibility() {
  for (const button of $("presets").querySelectorAll("button")) {
    const preset = PRESETS.find((item) => item[0] === button.textContent);
    if (!preset || !preset[1]) {
      button.hidden = false;
      continue;
    }
    button.hidden = !Object.keys(preset[1]).some((id) => byId.has(id));
  }
}

function updateBrand(info) {
  $("model-name").textContent = info.name;
  $("model-mark").textContent = info.mark || info.name.slice(0, 1);
  document.title = info.name + " · 虚拟主播";
}

async function main() {
  if (!window.Live2DCubismCore || !window.PIXI || !PIXI.live2d) {
    throw new Error("Live2D 运行时没有加载完成");
  }
  const data = await (await fetch("/api/models")).json();
  catalog = data.models || [];
  const pick = $("model-pick");
  for (const item of catalog) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    option.disabled = !item.ready;
    pick.appendChild(option);
  }
  pick.addEventListener("change", () => {
    showModel(pick.value).catch(showError);
  });
  buildPresetButtons();
  bindStagePointer();
  bindToggles();
  bindVoices();
  bindTalk();
  const stage = $("stage");
  app = new PIXI.Application({
    backgroundAlpha: 0,
    antialias: true,
    autoDensity: true,
    resolution: Math.min(window.devicePixelRatio || 1, 2),
    resizeTo: stage,
  });
  stage.appendChild(app.view);
  window.addEventListener("pointerup", () => {
    draggingIndex = -1;
  });
  window.addEventListener("resize", layoutModel);
  setInterval(syncSliders, 120);
  expose();
  const requested = new URLSearchParams(location.search).get("model");
  const initial = catalog.find((item) => item.id === requested && item.ready)
    || catalog.find((item) => item.ready);
  if (!initial) throw new Error("没有可用的模型");
  await showModel(initial.id);
}

/** 给试验和后续驱动留的手动入口，不参与画面逻辑。 */
function expose() {
  window.stage = {
    set: setParam,
    unlock,
    reset: resetAll,
    dump() {
      return params.map((item) => ({
        id: item.id,
        name: item.name,
        group: item.groupId,
        min: item.min,
        max: item.max,
        def: item.def,
        drawn: lastDrawn.get(item.index),
        locked: locks.has(item.index) ? locks.get(item.index) : null,
      }));
    },
    layout: layoutModel,
    setView(nextZoom, x, y) {
      zoom = nextZoom;
      panX = x || 0;
      panY = y || 0;
      layoutModel();
    },
    getView() {
      return { zoom: zoom, x: panX, y: panY };
    },
    use(modelId) {
      return showModel(modelId);
    },
    current() {
      return currentModel;
    },
    play: playPerformance,
  };
}

const speech = {
  mood: {},
  shown: {},
  cues: [],
  mouth: [],
  lines: [],
  lineIndex: 0,
  // 整体动态跨句保持。vibeFrom 是正在淡出的上一套循环。
  vibe: null,
  vibeFrom: null,
  vibeAt: 0,
  actFade: null,
  actFadeAt: 0,
  audio: null,
  analyser: null,
  sourceNode: null,
  freq: null,
  audioCtx: null,
  startedAt: 0,
  last: 0,
  duration: 1,
  active: false,
  level: 0,
  token: 0,
  history: [],
  busy: false,
  notice: "",
  // 没说完的时候，新生成的话只排队，不能一起开口。
  live: true,
  queue: [],
  fetching: false,
  analyserOn: false,
  replyId: null,
  board: [],
};

/** 换一套整体动态。同一套不重来，避免每句都抖一下。 */
function adoptVibe(spec) {
  if (!spec || !spec.id) return;
  if (speech.vibe && speech.vibe.id === spec.id) {
    speech.vibe = spec;
    return;
  }
  speech.vibeFrom = speech.vibe;
  speech.vibe = spec;
  speech.vibeAt = performance.now();
}

function sampleLoop(spec, now) {
  const offsets = {};
  if (!spec || !spec.tracks) return offsets;
  const period = Number(spec.period) > 0 ? Number(spec.period) : 1;
  const unit = ((now / 1000) % period) / period;
  for (const [id, keys] of Object.entries(spec.tracks)) {
    offsets[id] = sampleTrack(keys, unit);
  }
  return offsets;
}

/** 上一套 vibe 和下一套在大约 1.4 秒里交叉淡化。 */
function vibeOffsets(now) {
  const next = sampleLoop(speech.vibe, now);
  const prev = sampleLoop(speech.vibeFrom, now);
  const elapsed = now - (speech.vibeAt || now);
  const unit = speech.vibeFrom ? Math.min(1, elapsed / 1400) : 1;
  const smooth = unit * unit * (3 - 2 * unit);
  const offsets = {};
  const ids = new Set([...Object.keys(next), ...Object.keys(prev)]);
  for (const id of ids) {
    const value = (prev[id] || 0) + ((next[id] || 0) - (prev[id] || 0)) * smooth;
    if (Math.abs(value) > 0.01) offsets[id] = value;
  }
  if (unit >= 1) speech.vibeFrom = null;
  return offsets;
}

function actOffsets(elapsed) {
  const extra = {};
  for (const cue of speech.cues) {
    const start = Number(cue.at) * speech.duration;
    if (elapsed < start || elapsed > start + cue.duration) continue;
    const progress = (elapsed - start) / (cue.duration || 1);
    for (const [id, keys] of Object.entries(cue.tracks || {})) {
      extra[id] = (extra[id] || 0) + sampleTrack(keys, progress);
    }
  }
  return extra;
}

/** 短动作被下一句打断时，把当前偏移收掉，而不是突然弹回。 */
function fadeActs(now) {
  const elapsed = speech.active
    ? ((speech.audio && speech.audio.currentTime) || (now - speech.startedAt) / 1000)
    : 0;
  speech.actFade = actOffsets(elapsed);
  speech.actFadeAt = now;
}

function fadingActOffsets(now) {
  if (!speech.actFade) return {};
  const unit = Math.min(1, (now - speech.actFadeAt) / 480);
  const remain = 1 - unit * unit * (3 - 2 * unit);
  if (unit >= 1) {
    speech.actFade = null;
    return {};
  }
  const offsets = {};
  for (const [id, value] of Object.entries(speech.actFade)) offsets[id] = value * remain;
  return offsets;
}

/** 口播期间写表情、短动作、整体动态和嘴型。表情和 vibe 的切换都是慢慢靠过去。 */
function applyPerformance(now) {
  if (!core) return;
  const dt = Math.min(0.05, Math.max(0, (now - (speech.last || now)) / 1000));
  speech.last = now;
  let elapsed = 0;
  if (speech.active) {
    if (speech.audio && speech.audio.currentTime) elapsed = speech.audio.currentTime;
    else elapsed = (now - speech.startedAt) / 1000;
  }
  const unit = speech.duration > 0 ? Math.min(1, Math.max(0, elapsed / speech.duration)) : 0;
  const ids = new Set([...Object.keys(speech.shown), ...Object.keys(speech.mood)]);
  for (const id of ids) {
    const item = byId.get(id);
    if (!item) continue;
    const goal = Object.prototype.hasOwnProperty.call(speech.mood, id) ? speech.mood[id] : item.def;
    const current = Object.prototype.hasOwnProperty.call(speech.shown, id) ? speech.shown[id] : item.def;
    // 大约一秒多靠到新表情，避免害羞和生气直接跳。
    const next = current + (goal - current) * Math.min(1, dt * 2.2);
    if (!Object.prototype.hasOwnProperty.call(speech.mood, id) && Math.abs(next - item.def) < 0.02) {
      delete speech.shown[id];
    } else {
      speech.shown[id] = next;
    }
  }
  const extra = speech.active ? actOffsets(elapsed) : {};
  for (const [id, value] of Object.entries(fadingActOffsets(now))) {
    extra[id] = (extra[id] || 0) + value;
  }
  for (const [id, value] of Object.entries(vibeOffsets(now))) {
    const item = byId.get(id);
    if (!item || locks.has(item.index)) continue;
    extra[id] = (extra[id] || 0) + value;
  }
  for (const [id, value] of Object.entries(speech.shown)) {
    if (speech.active && (id === "ParamMouthOpenY" || id === "ParamMouthForm")) continue;
    const item = byId.get(id);
    if (!item) continue;
    core.setParameterValueByIndex(item.index, clamp(item, value + (extra[id] || 0)));
    delete extra[id];
  }
  for (const [id, value] of Object.entries(extra)) {
    const item = byId.get(id);
    if (!item) continue;
    const base = core.getParameterValueByIndex(item.index);
    core.setParameterValueByIndex(item.index, clamp(item, base + value));
  }
  if (speech.active) {
    const mouth = currentMouth(unit);
    const open = byId.get("ParamMouthOpenY");
    const form = byId.get("ParamMouthForm");
    if (open) core.setParameterValueByIndex(open.index, clamp(open, mouth.open));
    if (form) core.setParameterValueByIndex(form.index, clamp(form, mouth.form));
  }
}

function sampleTrack(keys, unit) {
  if (!keys || !keys.length) return 0;
  const u = Math.min(1, Math.max(0, unit));
  if (u <= keys[0][0]) return keys[0][1];
  for (let index = 1; index < keys.length; index += 1) {
    if (u <= keys[index][0]) {
      const span = keys[index][0] - keys[index - 1][0] || 1;
      const t = (u - keys[index - 1][0]) / span;
      return keys[index - 1][1] + (keys[index][1] - keys[index - 1][1]) * t;
    }
  }
  return keys[keys.length - 1][1];
}

function sampleMouth(unit) {
  const points = speech.mouth;
  if (!points.length) return { open: 0, form: 0 };
  return {
    open: sampleTrack(points.map((item) => [item.t, item.open]), unit),
    form: sampleTrack(points.map((item) => [item.t, item.form]), unit),
  };
}

function analyserOpen() {
  if (!speech.analyser) return 0;
  if (!speech.freq || speech.freq.length !== speech.analyser.fftSize) {
    speech.freq = new Uint8Array(speech.analyser.fftSize);
  }
  speech.analyser.getByteTimeDomainData(speech.freq);
  let sum = 0;
  for (let index = 0; index < speech.freq.length; index += 1) {
    const sample = (speech.freq[index] - 128) / 128;
    sum += sample * sample;
  }
  const rms = Math.sqrt(sum / speech.freq.length);
  const open = Math.min(1, Math.max(0, (rms - 0.015) * 8));
  speech.level += (open - speech.level) * 0.5;
  return speech.level;
}

function currentMouth(unit) {
  // 扁圆只看文本音素。音量只在确实接到这一句时才把门关上。
  const planned = sampleMouth(unit);
  const bias = speech.mouthBias || 0;
  const form = planned.form + bias;
  if (!speech.audio || !speech.analyserOn) return { open: planned.open, form };
  const loud = Math.min(1, analyserOpen() / 0.45);
  const playing = speech.audio.currentTime > 0.05 && !speech.audio.paused;
  if (!playing || loud < 0.04) return { open: planned.open, form };
  const gate = 0.2 + 0.8 * loud;
  return { open: planned.open * gate, form };
}

/** 在点击当帧解锁音频，避免等语音合成回来后被浏览器静音。 */
function unlockAudio() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return;
  if (!speech.audioCtx) speech.audioCtx = new Ctx();
  if (speech.audioCtx.state === "suspended") speech.audioCtx.resume();
}

function wireAnalyser(audio) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return false;
  if (!speech.audioCtx) speech.audioCtx = new Ctx();
  if (speech.audioCtx.state === "suspended") speech.audioCtx.resume();
  // 上下文没跑起来时不要把声音接走，否则扬声器是静音的。
  if (speech.audioCtx.state !== "running") return false;
  try {
    const source = speech.audioCtx.createMediaElementSource(audio);
    if (!speech.analyser) {
      speech.analyser = speech.audioCtx.createAnalyser();
      speech.analyser.fftSize = 1024;
      speech.analyser.connect(speech.audioCtx.destination);
    }
    source.connect(speech.analyser);
    speech.sourceNode = source;
    speech.analyserOn = true;
    return true;
  } catch (error) {
    speech.analyserOn = false;
    return false;
  }
}

/** 停掉正在响的那一句，避免两段声音叠在一起。 */
function haltAudio() {
  speech.analyserOn = false;
  if (speech.sourceNode) {
    try { speech.sourceNode.disconnect(); } catch (error) {}
    speech.sourceNode = null;
  }
  if (!speech.audio) return;
  speech.audio.onended = null;
  speech.audio.pause();
  speech.audio.removeAttribute("src");
  try { speech.audio.load(); } catch (error) {}
  speech.audio = null;
}

/** 合成和思考的提示写在模型下面的字幕栏，不叠在幕布上。 */
function setCue(text) {
  const cue = $("cue");
  if (cue) cue.textContent = text || "";
}

function finishSpeech() {
  fadeActs(performance.now());
  speech.active = false;
  speech.level = 0;
  speech.cues = [];
  // 这一轮说完，表情再缓回默认。vibe 留着，下一轮可以接着晃。
  speech.mood = {};
  if (!speech.queue.length && speech.replyId != null) {
    noteDanmakuDone(speech.replyId);
    speech.replyId = null;
    renderDanmaku(speech.board);
  }
  const token = speech.token;
  if (speech.queue.length) {
    pumpSpeech();
    return;
  }
  if (speech.live) armLive();
  window.setTimeout(() => {
    if (speech.token === token && !speech.active && $("subtitle")) {
      $("subtitle").textContent = "";
      setCue("");
    }
  }, 1400);
}

function stopSpeech() {
  speech.token += 1;
  speech.active = false;
  speech.queue = [];
  speech.lines = [];
  speech.mood = {};
  speech.cues = [];
  speech.mouth = [];
  speech.mouthBias = 0;
  speech.actFade = null;
  speech.level = 0;
  haltAudio();
  if ($("subtitle")) $("subtitle").textContent = "";
  setCue("");
}

/** 还在说就排到后面；没在说才开口。 */
function enqueueSpeech(data) {
  if (!data || data.idle) return;
  speech.queue.push(data);
  pumpSpeech();
}

function pumpSpeech() {
  if (speech.active) return;
  const data = speech.queue.shift();
  if (!data) return;
  beginPerformance(data);
}

function beginPerformance(data) {
  const token = ++speech.token;
  haltAudio();
  fadeActs(performance.now());
  speech.lines = Array.isArray(data.lines) && data.lines.length ? data.lines : [data];
  speech.lineIndex = -1;
  speech.notice = "";
  speech.level = 0;
  if (data.vibe) adoptVibe(data.vibe);
  if (data.stage && window.stageFreedom) window.stageFreedom.apply(data.stage);
  if (data.heard && data.heard.id != null) {
    if (speech.replyId != null && speech.replyId !== data.heard.id) noteDanmakuDone(speech.replyId);
    speech.replyId = data.heard.id;
    renderDanmaku(speech.board);
  }
  if (data.webError && data.channel !== "web") toast("网页不可用，已改用 API：" + data.webError);
  startLine(token, 0, data);
  if (speech.live && !speech.queue.length) armLive();
}

function playPerformance(data) {
  enqueueSpeech(data);
}

/** 播队列里的一句。说完再叫下一句，表情从上一句的脸上缓过去。 */
function startLine(token, index, reply) {
  if (speech.token !== token) return;
  const line = speech.lines[index];
  if (!line) {
    finishSpeech();
    return;
  }
  if (speech.lineIndex >= 0) fadeActs(performance.now());
  haltAudio();
  speech.lineIndex = index;
  if (line.vibe) adoptVibe(line.vibe);
  speech.mood = line.moodPose || {};
  if (window.stageClips) window.stageClips.play(line);
  speech.cues = line.cues || [];
  speech.mouth = line.mouth || [];
  speech.mouthBias = line.mouthBias || 0;
  speech.duration = line.estimate || 1.2;
  speech.active = true;
  speech.startedAt = performance.now();
  speech.last = speech.startedAt;
  speech.audio = null;
  $("subtitle").textContent = line.say || "";
  const vibeLabel = (line.vibe && line.vibe.label) || (speech.vibe && speech.vibe.label) || "";
  const progress = speech.lines.length > 1 ? (index + 1) + "/" + speech.lines.length : "";
  setCue([vibeLabel, progress].filter(Boolean).join(" · "));
  const acts = (line.cues || []).map((cue) => cue.act).filter(Boolean);
  const source = reply || {};
  if (!line.audio && line.audioError) {
    speech.notice = "语音没有合成：" + line.audioError;
    $("status").textContent = speech.notice;
    toast(speech.notice);
  } else if (!speech.notice) {
    const via = source.via === "harness" ? "Harness" : "";
    const channel = source.channel === "web" ? "网页" : (source.via === "harness" ? "API" : "");
    const heard = source.heard && source.heard.uname ? "回 " + source.heard.uname : "";
    $("status").textContent = [heard, via, channel, vibeLabel, line.mood, acts.join("、"), line.voiceLabel, line.lang].filter(Boolean).join(" · ");
  }
  const goNext = () => {
    if (speech.token !== token || speech.lineIndex !== index) return;
    if (index + 1 < speech.lines.length) startLine(token, index + 1, reply);
    else finishSpeech();
  };
  if (!line.audio) {
    window.setTimeout(goNext, speech.duration * 1000 + 180);
    return;
  }
  const audio = new Audio(line.audio);
  speech.audio = audio;
  speech.analyserOn = wireAnalyser(audio);
  audio.onloadedmetadata = () => {
    if (speech.token === token && Number.isFinite(audio.duration) && audio.duration > 0) {
      speech.duration = audio.duration;
    }
  };
  audio.onended = goNext;
  audio.play().catch((error) => {
    speech.notice = "没能播放声音：" + (error.message || "浏览器拦住了");
    $("status").textContent = speech.notice;
    toast(speech.notice);
    goNext();
  });
}

async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "请求失败");
  return data;
}

function talkText() {
  const text = $("talk-text").value.trim();
  if (text) return text;
  toast("输入框是空的，先说默认的一句");
  const sample = "<shy><nod>你好呀，今天天气真好。";
  $("talk-text").value = sample;
  return sample;
}

function voiceBody() {
  return {
    voice: $("talk-voice").value,
    pitch: Number($("talk-pitch").value),
    rate: Number($("talk-rate").value),
  };
}

/** 记住上次选的嗓音、音高和语速。 */
function saveVoicePref() {
  localStorage.setItem("chacha-voice", JSON.stringify(voiceBody()));
}

function showVoiceNumbers() {
  const pitch = Number($("talk-pitch").value);
  const rate = Number($("talk-rate").value);
  $("talk-pitch-val").textContent = (pitch > 0 ? "+" : "") + pitch;
  $("talk-rate-val").textContent = (rate > 0 ? "+" : "") + rate + "%";
}

/** 从动作清单填音色下拉。换音色时带上它自己的音高和语速。 */
async function bindVoices() {
  const select = $("talk-voice");
  let presets = [];
  try {
    const data = await (await fetch("/api/catalog")).json();
    presets = data.voicePresets || [];
  } catch (error) {
    presets = [];
  }
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("chacha-voice") || "null");
  } catch (error) {
    saved = null;
  }
  select.innerHTML = "";
  for (const item of presets) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.label;
    option.dataset.pitch = String(item.pitch);
    option.dataset.rate = String(item.rate);
    select.appendChild(option);
  }
  const picked = presets.find((item) => saved && item.id === saved.voice) || presets[0];
  if (picked) {
    select.value = picked.id;
    $("talk-pitch").value = saved && saved.voice === picked.id ? saved.pitch : picked.pitch;
    $("talk-rate").value = saved && saved.voice === picked.id ? saved.rate : picked.rate;
  }
  showVoiceNumbers();
  select.addEventListener("change", () => {
    const option = select.selectedOptions[0];
    if (option) {
      $("talk-pitch").value = option.dataset.pitch;
      $("talk-rate").value = option.dataset.rate;
    }
    showVoiceNumbers();
    saveVoicePref();
  });
  $("talk-pitch").addEventListener("input", () => {
    showVoiceNumbers();
    saveVoicePref();
  });
  $("talk-rate").addEventListener("input", () => {
    showVoiceNumbers();
    saveVoicePref();
  });
}

/** 页脚只写连接状态。气泡在舞台右侧，原文画在那里。 */
function showDanmaku(info) {
  const el = $("danmaku-state");
  if (!info) return;
  if (el) {
    if (!speech.live) el.textContent = "没在接弹幕";
    else if (!info.connected) el.textContent = info.hint || "弹幕机还没连上";
    else el.textContent = info.pending ? "弹幕已接上 · 待回 " + info.pending : "弹幕已接上";
  }
  if (Array.isArray(info.board)) {
    speech.board = info.board;
    renderDanmaku(info.board);
  }
}

/** 把一轮回复记进对话，给下一轮当上下文。 */
function rememberTurn(data, userText) {
  if (userText) speech.history.push({ role: "user", content: userText });
  else if (data.heard && data.heard.msg) {
    speech.history.push({ role: "user", content: "观众" + (data.heard.uname || "") + "：" + data.heard.msg });
  }
  if (data.say) speech.history.push({ role: "assistant", content: data.say });
  if (speech.history.length > 8) speech.history.splice(0, speech.history.length - 8);
}

/** 空闲或正在说的时候预取下一条。生成结果只排队，不打断当前这句。 */
async function armLive() {
  if (!speech.live || speech.fetching || speech.queue.length) return;
  if (speech.busy && !speech.active) return;
  speech.fetching = true;
  try {
    const data = await postJson("/api/chat", {
      live: true,
      lang: $("talk-lang").value,
      history: speech.history,
      freedom: window.stageFreedom ? window.stageFreedom.enabled() : false,
      ...voiceBody(),
    });
    showDanmaku(data.danmaku);
    if (data.idle) return;
    rememberTurn(data);
    speech.queue.push(data);
    pumpSpeech();
  } catch (error) {
    const message = error.message || "弹幕没接上";
    if (speech.liveError !== message) {
      speech.liveError = message;
      const el = $("danmaku-state");
      if (el) el.textContent = message;
    }
  } finally {
    speech.fetching = false;
  }
}

function bindTalk() {
  const speak = $("btn-speak");
  const ask = $("btn-ask");
  speak.addEventListener("click", async () => {
    const text = talkText();
    unlockAudio();
    speak.disabled = true;
    speech.busy = true;
    $("status").textContent = "正在合成语音…";
    setCue("正在合成语音…");
    try {
      const data = await postJson("/api/perform", { text, lang: $("talk-lang").value, ...voiceBody() });
      enqueueSpeech(data);
    } catch (error) {
      $("status").textContent = error.message || "直接说失败";
      setCue("");
      showError(error);
    } finally {
      speech.busy = false;
      speak.disabled = false;
    }
  });
  ask.addEventListener("click", async () => {
    const text = $("ask-text").value.trim();
    if (!text) {
      toast("先写一句，再发给 dsh");
      return;
    }
    unlockAudio();
    ask.disabled = true;
    speak.disabled = true;
    speech.busy = true;
    speech.notice = "";
    $("status").textContent = "dsh 正在写下一句…";
    setCue("dsh 正在写下一句…");
    try {
      const data = await postJson("/api/chat", {
        text,
        lang: $("talk-lang").value,
        history: speech.history,
        freedom: window.stageFreedom ? window.stageFreedom.enabled() : false,
        ...voiceBody(),
      });
      rememberTurn(data, text);
      $("talk-text").value = data.say || "";
      $("ask-text").value = "";
      enqueueSpeech(data);
    } catch (error) {
      const message = error.message || "DeepSeek 没有答上来";
      speech.notice = message;
      setCue("");
      $("status").textContent = message;
      $("error").hidden = false;
      $("error").textContent = message;
    } finally {
      speech.busy = false;
      ask.disabled = false;
      speak.disabled = false;
    }
  });
  $("ask-text").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      ask.click();
    }
  });
  const webLogin = $("btn-web-login");
  if (webLogin) {
    webLogin.addEventListener("click", async () => {
      webLogin.disabled = true;
      try {
        await postJson("/api/web-login", {});
        toast("已打开 Chrome。请在那个窗口登录，登录后它会自己记下。");
      } catch (error) {
        toast(error.message || "没能打开网页登录");
      } finally {
        webLogin.disabled = false;
      }
    });
  }
  const live = $("opt-live");
  if (live) {
    speech.live = live.checked;
    live.addEventListener("change", () => {
      speech.live = live.checked;
      if (!speech.live) {
        speech.queue = [];
        showDanmaku({ connected: false });
      } else armLive();
    });
  }
  window.setInterval(() => {
    if (!speech.live) return;
    pumpSpeech();
    armLive();
    refreshDanmaku();
  }, 1500);
}

loadStatus();
bindHud();
bindDanmaku();
main().catch(showError);

/** 弹幕层有固定宽高。条数决定窗口里留几条，多出来的往上滚。 */
function bindDanmaku() {
  const layer = $("danmaku-layer");
  const stage = $("stage");
  const scale = $("dm-scale");
  const show = $("dm-show");
  const count = $("dm-count");
  if (!layer || !stage || !scale || !show || !count) return;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("chacha-danmaku") || "{}"); } catch (error) { saved = {}; }
  if (saved.scale) scale.value = saved.scale;
  if (Number.isFinite(saved.count)) count.value = String(Math.max(1, Math.min(20, saved.count)));
  if (typeof saved.hidden === "boolean") show.checked = !saved.hidden;
  function applyScale() {
    layer.classList.toggle("is-hidden", !show.checked);
    layer.style.transform = "scale(" + (Number(scale.value) || 1) + ")";
    fitDanmaku();
  }
  function persist() {
    localStorage.setItem("chacha-danmaku", JSON.stringify({
      x: layer.offsetLeft,
      y: layer.offsetTop,
      scale: Number(scale.value),
      count: dmLimit(),
      hidden: !show.checked,
    }));
  }
  function placeDefault() {
    if (Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
      layer.style.left = saved.x + "px";
      layer.style.top = saved.y + "px";
      return;
    }
    const width = stage.clientWidth || layer.parentElement.clientWidth || 0;
    layer.style.left = Math.max(8, width - 292) + "px";
    layer.style.top = "16px";
  }
  placeDefault();
  applyScale();
  scale.addEventListener("input", () => { applyScale(); persist(); });
  show.addEventListener("change", () => { applyScale(); persist(); });
  count.addEventListener("change", () => { applyScale(); persist(); });
  count.addEventListener("input", () => { fitDanmaku(); persist(); });

  let drag = null;
  layer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const handle = event.target.closest && event.target.closest(".dm, .dm-grip");
    if (!handle) return;
    event.stopPropagation();
    event.preventDefault();
    drag = { x: event.clientX, y: event.clientY, left: layer.offsetLeft, top: layer.offsetTop };
    try { layer.setPointerCapture(event.pointerId); } catch (error) {}
  });
  layer.addEventListener("pointermove", (event) => {
    if (!drag) return;
    event.stopPropagation();
    const rect = stage.getBoundingClientRect();
    const zoom = Number(scale.value) || 1;
    let left = drag.left + event.clientX - drag.x;
    let top = drag.top + event.clientY - drag.y;
    const maxLeft = Math.max(0, rect.width - layer.offsetWidth * zoom);
    const maxTop = Math.max(0, rect.height - layer.offsetHeight * zoom);
    left = Math.min(maxLeft, Math.max(0, left));
    top = Math.min(maxTop, Math.max(0, top));
    layer.style.left = left + "px";
    layer.style.top = top + "px";
  });
  const endDrag = (event) => {
    if (!drag) return;
    drag = null;
    event.stopPropagation();
    persist();
  };
  layer.addEventListener("pointerup", endDrag);
  layer.addEventListener("pointercancel", endDrag);
  layer.addEventListener("wheel", (event) => {
    const list = $("danmaku-list");
    event.stopPropagation();
    if (list && list.scrollHeight > list.clientHeight + 1) return;
    event.preventDefault();
    const next = (Number(scale.value) || 1) * (event.deltaY > 0 ? 0.92 : 1.08);
    scale.value = String(Math.min(2, Math.max(0.6, Math.round(next * 20) / 20)));
    applyScale();
    persist();
  }, { passive: false });
}

/** 显示条数。没填或填坏时按 5 条。 */
function dmLimit() {
  const input = $("dm-count");
  const count = Number(input && input.value);
  if (!Number.isFinite(count)) return 5;
  return Math.max(1, Math.min(20, Math.round(count)));
}

/** 窗口高度按最近几条撑开，多出来的留在上面，滚到最底下。 */
function fitDanmaku() {
  const list = $("danmaku-list");
  if (!list) return;
  const kids = [...list.children];
  if (!kids.length) {
    list.style.height = "0px";
    return;
  }
  const visible = kids.slice(-dmLimit());
  let height = 0;
  visible.forEach((el, index) => {
    height += el.offsetHeight;
    if (index) height += 8;
  });
  list.style.width = "280px";
  list.style.height = Math.max(height, 16) + "px";
  list.scrollTop = list.scrollHeight;
}

/** 舞台左栏开关状态栏。血条蓝条可拖、可缩放，位置记在本地。 */
function bindHud() {
  const hud = $("hud");
  const stage = $("stage");
  const show = $("hud-show");
  const style = $("hud-style");
  const scale = $("hud-scale");
  const nums = $("hud-nums");
  if (!hud || !show || !style || !scale) return;
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("chacha-hud") || "{}"); } catch (error) { saved = {}; }
  if (saved.style && style.querySelector("option[value='" + saved.style + "']")) style.value = saved.style;
  if (saved.scale) scale.value = saved.scale;
  if (nums && (saved.nums === "always" || saved.nums === "hover")) nums.value = saved.nums;
  if (typeof saved.hidden === "boolean") show.checked = !saved.hidden;
  if (Number.isFinite(saved.x)) hud.style.left = saved.x + "px";
  if (Number.isFinite(saved.y)) hud.style.top = saved.y + "px";

  function persist() {
    localStorage.setItem("chacha-hud", JSON.stringify({
      x: hud.offsetLeft,
      y: hud.offsetTop,
      scale: Number(scale.value),
      style: style.value,
      nums: nums ? nums.value : "hover",
      hidden: !show.checked,
    }));
  }
  function applyHud() {
    const always = nums && nums.value === "always";
    hud.className = "hud style-" + (style.value || "jrpg")
      + (always ? " show-values" : "")
      + (show.checked ? "" : " is-hidden");
    hud.style.transform = "scale(" + (Number(scale.value) || 1) + ")";
  }
  applyHud();
  show.addEventListener("change", () => { applyHud(); persist(); });
  style.addEventListener("change", () => { applyHud(); persist(); });
  if (nums) nums.addEventListener("change", () => { applyHud(); persist(); });
  scale.addEventListener("input", () => { applyHud(); persist(); });

  let drag = null;
  hud.addEventListener("pointerdown", (event) => {
    event.stopPropagation();
    drag = { x: event.clientX, y: event.clientY, left: hud.offsetLeft, top: hud.offsetTop };
    try { hud.setPointerCapture(event.pointerId); } catch (error) {}
  });
  hud.addEventListener("pointermove", (event) => {
    if (!drag) return;
    event.stopPropagation();
    const rect = stage.getBoundingClientRect();
    const zoomHud = Number(scale.value) || 1;
    let left = drag.left + event.clientX - drag.x;
    let top = drag.top + event.clientY - drag.y;
    const maxLeft = Math.max(0, rect.width - hud.offsetWidth * zoomHud);
    const maxTop = Math.max(0, rect.height - hud.offsetHeight * zoomHud);
    left = Math.min(maxLeft, Math.max(0, left));
    top = Math.min(maxTop, Math.max(0, top));
    hud.style.left = left + "px";
    hud.style.top = top + "px";
  });
  const endDrag = (event) => {
    if (!drag) return;
    drag = null;
    event.stopPropagation();
    persist();
  };
  hud.addEventListener("pointerup", endDrag);
  hud.addEventListener("pointercancel", endDrag);
  hud.addEventListener("wheel", (event) => event.stopPropagation(), { passive: true });

  function paintMeter(node, ratio) {
    const fill = node && node.querySelector("i");
    if (!fill) return;
    if (ratio == null || !Number.isFinite(Number(ratio))) {
      fill.style.width = "0%";
      const missing = node.querySelector(".val");
      if (missing) missing.textContent = "--";
      return;
    }
    fill.style.width = Math.max(0, Math.min(100, Number(ratio) * 100)) + "%";
    const val = node.querySelector(".val");
    if (val) val.textContent = String(Math.round(Number(ratio) * 100));
  }
  async function refreshHud() {
    try {
      const data = await (await fetch("/api/hud")).json();
      paintMeter(hud.querySelector(".hp"), data.hp);
      paintMeter(hud.querySelector(".mp"), data.mp);
      const hp = hud.querySelector(".hp");
      const mp = hud.querySelector(".mp");
      if (hp) hp.title = data.hp == null ? (data.hpError || "余额未知") : ("余额 " + data.yuan + " 元，100 元满血");
      if (mp) {
        const pct = Math.round((Number(data.mp) || 0) * 100);
        mp.title = data.compacted ? ("上下文余量 " + pct + "%，这一轮压缩过") : ("上下文余量 " + pct + "%");
      }
    } catch (error) {}
  }
  refreshHud();
  window.setInterval(refreshHud, 20000);
}

const _seenDanmaku = new Set();

function escapeText(text) {
  return String(text || "").replace(/[&<>"]/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
  }[ch]));
}

/** 舞台上的气泡。正在回的是金边，排队的是绿边。有内容时藏起「弹幕」抓手。 */
function renderDanmaku(items) {
  const list = $("danmaku-list");
  const layer = $("danmaku-layer");
  if (!list || !layer) return;
  const grip = layer.querySelector(".dm-grip");
  const rows = items || [];
  if (grip) grip.hidden = rows.length > 0;
  list.innerHTML = rows.map((item) => {
    const fresh = !_seenDanmaku.has(item.id);
    _seenDanmaku.add(item.id);
    let state = item.state || "new";
    if (speech.replyId != null && item.id === speech.replyId) state = "speaking";
    const tag = state === "speaking" ? "在回" : (state === "queued" ? "排队" : "");
    const name = escapeText(item.uname || "观众");
    const mark = name.slice(0, 1) || "观";
    return (
      "<article class=\"dm dm-" + state + (fresh ? " dm-in" : "") + "\">" +
      "<div class=\"dm-ava\">" + escapeText(mark) + "</div>" +
      "<div class=\"dm-body\"><div class=\"dm-name\">" + name + "</div>" +
      "<div class=\"dm-text\">" + escapeText(item.msg || "") +
      (tag ? "<span class=\"dm-tag\">" + tag + "</span>" : "") +
      "</div></div></article>"
    );
  }).join("");
  fitDanmaku();
}

async function refreshDanmaku() {
  try {
    const status = await (await fetch("/api/status")).json();
    showDanmaku(status.danmaku);
  } catch (error) {}
}

function noteDanmakuDone(id) {
  if (id == null) return;
  fetch("/api/danmaku/done", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: id }),
  }).then((response) => response.json()).then((data) => {
    if (data && data.danmaku) showDanmaku(data.danmaku);
  }).catch(() => {});
}
