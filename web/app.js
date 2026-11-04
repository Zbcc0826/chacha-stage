/* 虚拟主播 / 桌宠引擎。
 * 滑杆范围直接读当前 moc。拖动即锁定，并在每帧绘制前写回。
 * 模型列表来自 /api/models。模型自带动作由 engine.js 播放。
 * 带 ?surface=pet 的是桌面浮窗，不参加直播间的口播轮询。
 * 带 ?surface=stage 的是全屏纯画面（OBS / 投屏），跟随控制页开播。
 */
const _surface = new URLSearchParams(location.search).get("surface") || "";
const desktopPet = _surface === "pet";
const stageCast = _surface === "stage";
window.chachaDesktopPet = desktopPet;
window.chachaStageCast = stageCast;

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
  const stage = $("stage");
  const viewW = stage && stage.clientWidth ? stage.clientWidth : app.screen.width;
  const viewH = stage && stage.clientHeight ? stage.clientHeight : app.screen.height;
  if (app.renderer && (Math.abs(app.screen.width - viewW) > 1 || Math.abs(app.screen.height - viewH) > 1)) {
    app.renderer.resize(viewW, viewH);
  }
  const iw = model.internalModel.originalWidth || model.internalModel.width || 1;
  const ih = model.internalModel.originalHeight || model.internalModel.height || 1;
  const fit = Math.min(viewW / iw, viewH / ih) * 0.9;
  model.scale.set(fit * zoom);
  if (model.anchor) model.anchor.set(0.5, 0.62);
  // 桌宠缩放以这个点为中心，比例要和 pet_window._resize_hwnd 一致。
  model.position.set(viewW * 0.5 + panX, viewH * 0.56 + panY);
}

function bindStagePointer() {
  const stage = $("stage");
  stage.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (window.chachaDesktopPet) {
      const box = $("pet-scale");
      const current = box ? Number(box.value) || 1 : 1;
      const next = Math.min(4, Math.max(0.6, Math.round(current * (event.deltaY > 0 ? 0.92 : 1.08) * 20) / 20));
      if (box) {
        box.value = String(next);
        box.dispatchEvent(new Event("input", { bubbles: true }));
      }
      return;
    }
    const next = zoom * (event.deltaY > 0 ? 0.92 : 1.08);
    zoom = Math.min(4, Math.max(0.25, next));
    layoutModel();
  }, { passive: false });
  stage.addEventListener("pointerdown", (event) => {
    if (window.chachaDesktopPet) return;
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
    // 眨眼由 applyAutoBlink 驱动，这里只复位计时。
    blinkClock = 0;
    blinkLastNow = 0;
  });
  $("opt-mouse").addEventListener("change", () => {
    if (!$("opt-mouse").checked && model) {
      model.internalModel.focusController.focus(0, 0, true);
    }
  });
  ["opt-physics", "opt-breath", "opt-sway", "opt-blink", "opt-mouse", "opt-ahoge"].forEach((id) => {
    const box = $(id);
    if (box) box.addEventListener("change", () => noteLook());
  });
  $("search").addEventListener("input", applyFilter);
  $("only-locked").addEventListener("change", applyFilter);
  $("btn-reset").addEventListener("click", resetAll);
  const reload = $("btn-reload");
  if (reload) reload.addEventListener("click", () => { reloadModel().catch(showError); });
  $("btn-copy").addEventListener("click", () => copyText(poseJson()));
  $("btn-catalog").addEventListener("click", () => copyText(catalogText()));
}

function applyRuntimeToggles() {
  model.internalModel.physics = $("opt-physics").checked ? physicsRef : null;
  // SDK 的 breath 会同时改头角和身体，试验参数时会把滑杆带跑。
  model.internalModel.breath = null;
  // Idle 等动作会占用眨眼通道，官方 update 里 `motion更新成功 || eyeBlink` 会跳过眨眼。
  // 因此不挂到 internalModel.eyeBlink，改由 applyAutoBlink 在绘制前强制推进。
  model.internalModel.eyeBlink = null;
}

let blinkClock = 0;
let blinkLastNow = 0;
/** 下一次眨眼前的随机间隔（秒），避免节奏机械。 */
let blinkGap = 2.4;

/**
 * 自动眨眼：必须在 applyPerformance / locks 之后调用，否则表情插值会把眼睛写回睁开。
 * 不用 SDK CubismEyeBlink（Idle 动作会短路；且 updateParameters 常停在全开）。
 * 只在「闭眼过程」写入，睁开阶段不碰参数，方便保留生气等半眯表情。
 */
function applyAutoBlink(now) {
  if (!$("opt-blink") || !$("opt-blink").checked || !core) return;
  const left = byId.get("ParamEyeLOpen");
  const right = byId.get("ParamEyeROpen");
  if (!left && !right) return;
  if ((left && locks.has(left.index)) || (right && locks.has(right.index))) return;

  const last = blinkLastNow || now;
  let dt = (now - last) / 1000;
  blinkLastNow = now;
  if (dt <= 0 || dt > 0.2) dt = 1 / 60;

  blinkClock += dt;
  // 闭眼曲线：下压 → 全闭停顿 → 抬起；间隔随机，避免像节拍器。
  // 全闭略拉长，261 这类细眼皮短闭几乎看不出。
  const closeDur = 0.09;
  const holdDur = 0.14;
  const openDur = 0.12;
  const blinkDur = closeDur + holdDur + openDur;
  if (blinkClock >= blinkGap + blinkDur) {
    blinkClock = 0;
    blinkGap = 2.0 + Math.random() * 3.0;
  }
  if (blinkClock < blinkGap) return;

  const t = blinkClock - blinkGap;
  let open = 1;
  if (t < closeDur) open = 1 - t / closeDur;
  else if (t < closeDur + holdDur) open = 0;
  else if (t < blinkDur) open = (t - closeDur - holdDur) / openDur;
  else return;

  // 取当前值与眨眼开合的较小者，避免把已眯上的眼睛强制撑开。
  if (left && !locks.has(left.index)) {
    const cur = core.getParameterValueByIndex(left.index);
    const next = left.min + open * (left.max - left.min);
    core.setParameterValueByIndex(left.index, Math.min(cur, next));
  }
  if (right && !locks.has(right.index)) {
    const cur = core.getParameterValueByIndex(right.index);
    const next = right.min + open * (right.max - right.min);
    core.setParameterValueByIndex(right.index, Math.min(cur, next));
  }
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

/** Cubism5 大模型偶发排序表全 0，导致只画到透明 mesh；按 renderOrders 重填。 */
function ensureDrawableSort() {
  if (!model || !model.internalModel || !core) return;
  const renderer = model.internalModel.renderer;
  const list = renderer && renderer._sortedDrawableIndexList;
  if (!list || !list.length) return;
  let zeros = 0;
  const probe = Math.min(list.length, 24);
  for (let i = 0; i < probe; i += 1) {
    if (list[i] === 0) zeros += 1;
  }
  if (zeros < probe - 1) return;
  const orders = core.getModel && core.getModel().drawables && core.getModel().drawables.renderOrders;
  if (!orders || orders.length !== list.length) return;
  for (let i = 0; i < orders.length; i += 1) list[orders[i]] = i;
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
  ensureDrawableSort();
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
  // 眨眼放在最后：表情/口型插值之后仍能压上眼皮。
  applyAutoBlink(now);
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

/**
 * 扩展裁剪遮罩排版：原版每通道最多 9/16 格（总计约 36/64），
 * 261琴这类 VTS 模型会到约 100 个，超限后 _layoutChannelNo 为空并整帧崩溃。
 * 这里按 N×N 网格继续排，并加大遮罩图集，保留遮罩而不是关掉。
 */
function patchHeavyClippingLayout(manager) {
  if (!manager || manager.__chachaLayoutPatched) return;
  manager.__chachaLayoutPatched = true;

  if (typeof manager.setClippingMaskBufferSize === "function") {
    manager.setClippingMaskBufferSize(4096);
  } else {
    manager._clippingMaskBufferSize = 4096;
  }

  if (manager.getChannelFlagAsColor) {
    const originalFlag = manager.getChannelFlagAsColor.bind(manager);
    manager.getChannelFlagAsColor = (index) => originalFlag(index) || { R: 1, G: 0, B: 0, A: 0 };
  }

  manager.setupLayoutBounds = function setupLayoutBoundsExtended(usingClipCount) {
    const list = this._clippingContextListForMask;
    if (!list || usingClipCount <= 0) return;
    let perChannel = (usingClipCount / 4) | 0;
    let rem = usingClipCount % 4;
    let cursor = 0;
    for (let channel = 0; channel < 4; channel += 1) {
      const count = perChannel + (channel < rem ? 1 : 0);
      if (count <= 0) continue;
      if (count === 1) {
        const clip = list[cursor++];
        clip._layoutChannelNo = channel;
        clip._layoutBounds.x = 0;
        clip._layoutBounds.y = 0;
        clip._layoutBounds.width = 1;
        clip._layoutBounds.height = 1;
        continue;
      }
      if (count === 2) {
        for (let i = 0; i < count; i += 1) {
          const clip = list[cursor++];
          clip._layoutChannelNo = channel;
          clip._layoutBounds.x = 0.5 * (i % 2);
          clip._layoutBounds.y = 0;
          clip._layoutBounds.width = 0.5;
          clip._layoutBounds.height = 1;
        }
        continue;
      }
      // 通用网格：保证格子数 >= count（100 遮罩时约每通道 25 → 5×5）
      const grid = Math.max(2, Math.ceil(Math.sqrt(count)));
      const cell = 1 / grid;
      for (let i = 0; i < count; i += 1) {
        const clip = list[cursor++];
        const col = i % grid;
        const row = (i / grid) | 0;
        clip._layoutChannelNo = channel;
        clip._layoutBounds.x = col * cell;
        clip._layoutBounds.y = row * cell;
        clip._layoutBounds.width = cell;
        clip._layoutBounds.height = cell;
      }
    }
  };
}

function enableHeavyClipping() {
  if (!model || !model.internalModel) return;
  const renderer = model.internalModel.renderer;
  if (!renderer || !renderer._clippingManager) return;
  // 若之前误关过遮罩，恢复核心标记。
  const modelCore = model.internalModel.coreModel;
  if (modelCore && modelCore.isUsingMasking && modelCore.isUsingMasking() === false) {
    delete modelCore.isUsingMasking;
  }
  patchHeavyClippingLayout(renderer._clippingManager);
  // 强制下一帧按新尺寸重建 mask FBO。
  renderer.firstDraw = true;
  if (renderer._clippingManager._maskRenderTexture) {
    renderer._clippingManager._maskRenderTexture = null;
  }
}

function hookModel() {
  const internal = model.internalModel;
  core = internal.coreModel;
  physicsRef = internal.physics || null;
  // SDK eyeBlink 会被 Idle 动作短路，且常停在全开；改由 applyAutoBlink 驱动。
  blinkClock = 0;
  blinkLastNow = 0;
  blinkGap = 1.2 + Math.random() * 1.5;
  applyRuntimeToggles();
  enableHeavyClipping();
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
  }
  params = rows;
  // 呆毛旋转只认参数名里带呆毛的模型。大肥鱼的 Param38 是手部开关，不是呆毛。
  ahoge = null;
  for (const item of rows) {
    if (!/呆毛|ahoge/i.test(item.name || "")) continue;
    ahoge = item;
    if (/旋|转/.test(item.name || "")) break;
  }
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
  noteLook();
}

function resetAll() {
  stopSpeech();
  locks.clear();
  resetBase = true;
  markPreset("默认");
  for (const item of params) item.row.classList.remove("locked");
  $("status").textContent = "已恢复默认姿态";
  applyFilter();
  noteLook();
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
  noteLook();
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
  noteLook();
  return { id, name: item.name, value: next, min: item.min, max: item.max, def: item.def };
}

async function loadStatus() {
  try {
    const status = await (await fetch("/api/status")).json();
    const pill = $("llm");
    pill.textContent = status.llmReady ? "dsh 已连接" : "dsh 未配置密钥";
    pill.classList.toggle("ready", !!status.llmReady);
    if (status.vibe) adoptVibe(status.vibe);
    adoptLiveSession(status);
    showDanmaku(status.danmaku);
    // 直播间刷新后默认停播；全屏画面/桌宠不改会话。
    if (!window.chachaDesktopPet && !window.chachaStageCast && status.liveSession) {
      postJson("/api/session", { live: false }).then((session) => {
        adoptLiveSession(session);
        speech.live = false;
        paintSession();
      }).catch(() => {});
    }
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
async function showModel(modelId, options) {
  const keepView = !!(options && options.keepView);
  // 重新加载时先记下位置和缩放，新模型摆完再放回去。
  const kept = keepView && window.stage && window.stage.getView ? window.stage.getView() : null;
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
  if (!keepView) {
    zoom = 1;
    panX = 0;
    panY = 0;
  }
  model = next;
  app.stage.addChild(model);
  hookModel();
  buildParams(labels);
  buildGroups(labels);
  layoutModel();
  window.stageModel = model;
  // 首帧后再确认一次排版补丁（renderer 有时稍晚才挂上 clippingManager）。
  enableHeavyClipping();
  requestAnimationFrame(() => enableHeavyClipping());
  model.eventMode = "static";
  window.dispatchEvent(new CustomEvent("stage-model"));
  if (kept && window.stage && window.stage.setView) window.stage.setView(kept.zoom, kept.x, kept.y);
  $("ahoge-toggle").hidden = !ahoge;
  refreshPresetVisibility();
  $("status").textContent = info.name + " 已加载 · 参数 " + params.length;
  noteLook();
}

/** 这套模型一个都对不上的预设不显示，例如 areazil 没有旋风呆毛。 */
function refreshPresetVisibility() {
  for (const button of $("presets").querySelectorAll("button")) {
    const preset = PRESETS.find((item) => item[0] === button.textContent);
    if (!preset || !preset[1]) {
      button.hidden = false;
      continue;
    }
    if (preset[0] === "旋风呆毛") {
      button.hidden = !ahoge;
      continue;
    }
    button.hidden = !Object.keys(preset[1]).some((id) => byId.has(id));
  }
}

/** 丢掉当前表情、动作和锁定，重新载入同一套模型。位置和缩放保持不动。 */
async function reloadModel() {
  if (!currentModel) return;
  const button = $("btn-reload");
  if (button) button.disabled = true;
  try {
    await showModel(currentModel.id, { keepView: true });
    $("status").textContent = currentModel.name + " 已回到默认状态";
  } finally {
    if (button) button.disabled = false;
  }
}

function updateBrand(info) {
  $("model-name").textContent = info.name;
  $("model-mark").textContent = info.mark || info.name.slice(0, 1);
  document.title = info.name + " · 虚拟主播";
}

/** 按模型库重画下拉框。当前选中的模型尽量留着。 */
function fillModelPick(models) {
  catalog = models || [];
  window.stageLibrary = catalog;
  const pick = $("model-pick");
  const current = pick.value;
  pick.replaceChildren();
  for (const item of catalog) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    option.disabled = !item.ready;
    pick.appendChild(option);
  }
  if ([...pick.options].some((option) => option.value === current)) pick.value = current;
  const browse = document.getElementById("btn-browse-model");
  if (browse && window.stagePicker) browse.textContent = window.stagePicker;
  if (window.renderModelLibrary) window.renderModelLibrary();
}

async function refreshModels() {
  const data = await (await fetch("/api/models")).json();
  if (data.picker) window.stagePicker = data.picker;
  fillModelPick(data.models || []);
  return data;
}
window.refreshModels = refreshModels;

async function main() {
  if (!window.Live2DCubismCore || !window.PIXI || !PIXI.live2d) {
    throw new Error("Live2D 运行时没有加载完成");
  }
  const data = await refreshModels();
  const pick = $("model-pick");
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
  // 暴露给调试；部分环境下 ticker 可能未自动转起来，这里强制启动渲染循环。
  window.app = app;
  if (app.ticker && !app.ticker.started) app.ticker.start();
  app.ticker.maxFPS = 0;
  stage.appendChild(app.view);
  window.addEventListener("pointerup", () => {
    draggingIndex = -1;
  });
  window.addEventListener("resize", () => {
    window.requestAnimationFrame(() => window.requestAnimationFrame(layoutModel));
  });
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
  actId: 0,
  seenActs: new Set(),
  // 刷新后先对齐服务端最新 actId，避免把缓存里旧口播再念一遍。
  cueReady: false,
  notice: "",
  // 没说完的时候，新生成的话只排队，不能一起开口。
  // 默认关着，点顶栏「开始」才开全套（接弹幕 + 找话题闲聊）。
  live: false,
  // 服务端开播世代：停止后递增，用来丢掉已生成但未播的直播句。
  liveSession: false,
  liveGen: 0,
  // 口播静音：控制页与全屏画面各自记住，只影响本页扬声器。
  muted: false,
  queue: [],
  fetching: false,
  analyserOn: false,
  replyId: null,
  board: [],
  // 冷场闲聊：约 10 秒没人说话就自己开口。
  lastSpokeAt: 0,
  fillerBusy: false,
  // 刚回完弹幕后，再等这么久看还有没有弹幕；期间不续闲聊。
  postDanmakuQuietUntil: 0,
  // 本轮口播是否来自弹幕/礼物/进房（用于结束后进入静默窗）。
  spokeDanmaku: false,
  // 弹幕复述句播完后，下一句前的思考停顿（毫秒）。
  pendingThinkPause: 0,
  // 复述已结束、回答句尚未入队时的挂起标记（与 token 对齐防串台）。
  thinkWaitToken: 0,
};

// 直播间和桌宠共用的形象、语音。桌宠先等直播间写入，避免一打开就把缩放和姿态盖掉。
const share = {
  lookRev: 0,
  micRev: 0,
  busy: false,
  quiet: false,
  lookReady: !(window.chachaDesktopPet || window.chachaStageCast),
  lastLook: "",
  owner: "web",
  panX: 0,
  panY: 0,
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
  const mouthDriving = speech.active;
  for (const [id, value] of Object.entries(speech.shown)) {
    if (mouthDriving && (id === "ParamMouthOpenY" || id === "ParamMouthForm")) continue;
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
  // 顺带解锁 HTMLAudio 自动播放策略（静音短音）。
  try {
    const quiet = new Audio(
      "data:audio/wav;base64,UklGRimAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA="
    );
    quiet.volume = 0.01;
    quiet.play().catch(() => {});
  } catch (error) {}
}

function wireAnalyser(audio) {
  // 不再把 HTMLAudio 接进 MediaElementSource：一旦接管失败或 CORS 不齐，浏览器会整段静音。
  // 嘴型改用口型时间轴（planned mouth），声音只走元素直出。
  speech.analyserOn = false;
  speech.sourceNode = null;
  return false;
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

/** 这一轮是礼物、进房还是普通弹幕。 */
function speechKind(data) {
  if (!data) return "";
  if (data.kind) return data.kind;
  if (data.heard && data.heard.kind) return data.heard.kind;
  return "";
}

/** 合成和思考的提示写在模型下面的字幕栏，不叠在幕布上。 */
function setCue(text) {
  const cue = $("cue");
  if (cue) cue.textContent = text || "";
}

/** 复述句播完后：若队列已有下一句则立刻「思考」停顿；若回答句还在路上则先挂起，等 enqueue 再停顿。 */
function runThinkPauseThenPump(token) {
  const pause = Number(speech.pendingThinkPause) || 0;
  speech.pendingThinkPause = 0;
  speech.thinkWaitToken = 0;
  if (pause <= 0) {
    pumpSpeech();
    return;
  }
  setCue("思考中…");
  $("status").textContent = "思考中…";
  window.setTimeout(() => {
    if (speech.token !== token) return;
    if (speech.active) return;
    pumpSpeech();
  }, pause);
}

function finishSpeech() {
  fadeActs(performance.now());
  speech.active = false;
  speech.level = 0;
  speech.cues = [];
  // 这一轮说完，表情再缓回默认。vibe 留着，下一轮可以接着晃。
  speech.mood = {};
  noteSpoke();
  // 说完收回模型自带表情/手部，避免一直挂着。
  if (window.stageClips && typeof window.stageClips.restoreClip === "function") {
    window.stageClips.restoreClip();
  } else if (window.stageClips && typeof window.stageClips.clearHand === "function") {
    window.stageClips.clearHand();
  }
  if (!speech.queue.length && speech.replyId != null && !speech.pendingThinkPause) {
    noteDanmakuDone(speech.replyId);
    speech.replyId = null;
    renderDanmaku(speech.board);
  }
  const token = speech.token;
  if (speech.queue.length) {
    runThinkPauseThenPump(token);
    return;
  }
  // 回答句可能还在合成/轮询路上：保留 pendingThinkPause，等 enqueueSpeech 再接停顿。
  if (speech.pendingThinkPause > 0) {
    speech.thinkWaitToken = token;
    setCue("思考中…");
    $("status").textContent = "思考中…";
    // 兜底：若后续没有下一句，几秒后取消挂起，避免卡死。
    window.setTimeout(() => {
      if (speech.thinkWaitToken !== token) return;
      if (speech.active || speech.queue.length) return;
      speech.pendingThinkPause = 0;
      speech.thinkWaitToken = 0;
      if (speech.replyId != null) {
        noteDanmakuDone(speech.replyId);
        speech.replyId = null;
        renderDanmaku(speech.board);
      }
      if (speech.spokeDanmaku) {
        speech.spokeDanmaku = false;
        speech.postDanmakuQuietUntil = performance.now() + 10000;
      }
      if (speech.live) armLive();
      window.setTimeout(resumeMic, 350);
    }, 6000);
    return;
  }
  // 回完弹幕后先静默 10 秒专等弹幕，不马上续自己的闲聊。
  if (speech.spokeDanmaku) {
    speech.spokeDanmaku = false;
    speech.postDanmakuQuietUntil = performance.now() + 10000;
  }
  if (speech.live) armLive();
  window.setTimeout(resumeMic, 350);
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
  speech.pendingThinkPause = 0;
  speech.thinkWaitToken = 0;
  haltAudio();
  if ($("subtitle")) $("subtitle").textContent = "";
  setCue("");
}


/** 还在说就排到后面；没在说才开口。复述后若在等思考停顿，先停再播。 */
function enqueueSpeech(data) {
  if (!data || data.idle) return;
  speech.queue.push(data);
  if (speech.active) return;
  if (speech.pendingThinkPause > 0) {
    runThinkPauseThenPump(speech.token);
    return;
  }
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
  noteSpoke();
  if (data.vibe) adoptVibe(data.vibe);
  if (data.stage && window.stageFreedom) {
    Promise.resolve(window.stageFreedom.apply(data.stage)).catch((error) => console.warn(error));
  }
  if (data.heard && data.heard.id != null) {
    if (speech.replyId != null && speech.replyId !== data.heard.id) noteDanmakuDone(speech.replyId);
    speech.replyId = data.heard.id;
    speech.spokeDanmaku = true;
    renderDanmaku(speech.board);
  } else if (!data.filler) {
    speech.spokeDanmaku = false;
  }
  // 普通弹幕复述后停顿。礼物致谢、进房欢迎不装作思考。
  const lineIndex = Number(data.lineIndex);
  const lineCount = Number(data.lineCount);
  const kind = speechKind(data);
  const wantThink = kind !== "gift" && kind !== "enter" && (
    !!data.thinkAfter || (!!data.heard && lineIndex === 0 && lineCount > 1)
  );
  if (wantThink) {
    speech.pendingThinkPause = 1100 + Math.random() * 700;
  }
  if (kind === "gift") setCue("感谢礼物");
  else if (kind === "enter") setCue("欢迎进场");
  else if (data.echo || (data.heard && lineIndex === 0)) setCue("复述弹幕…");
  if (data.webError && data.channel !== "web") toast("网页不可用，已改用 API：" + data.webError);
  // 闲聊轮：没带表情时按台词内容补一个，不再随机甩动作。
  if (data.filler) {
    const line = speech.lines[0];
    if (line && !line.expression) {
      const id = pickClipForSpeech(line.say || data.say || "", line.mood);
      if (id) line.expression = id;
    }
  }
  startLine(token, 0, data);
  if (speech.live && !speech.queue.length) armLive();
}

function playPerformance(data) {
  deliverSpeech(data);
}

function petIsShowing() {
  if (window.chachaDesktopPet) return false;
  const button = $("mode-pet");
  return !!(button && button.getAttribute("aria-pressed") === "true");
}

/** 是否允许自动播这条（弹幕/闲聊）。未开播只推进游标，不念。 */
function isLiveAutoAct(data) {
  if (!data) return false;
  if (data.autoLive === true || data.liveGen != null) return true;
  if (data.filler === true) return true;
  if (data.heard && data.heard.id != null) return true;
  return false;
}

function liveAutoAllowed() {
  // 桌宠 / 全屏画面跟随服务端开播；控制页看本地点的「开始」。
  if (window.chachaDesktopPet || window.chachaStageCast) return !!speech.liveSession;
  return !!speech.live;
}

/** 同一轮口播只播一次。force 用于手动「直接说 / 提问」。 */
function deliverSpeech(data, opts) {
  if (!data || data.idle || data.streamed) return;
  const force = !!(opts && opts.force);
  if (Array.isArray(data.batch)) {
    for (const item of data.batch) deliverSpeech(item, opts);
    return;
  }
  if (data.actId != null) {
    const id = Number(data.actId);
    if (speech.seenActs.has(id)) return;
    speech.seenActs.add(id);
    speech.actId = id;
  }
  // 未开播：自动直播句只吞掉编号，不排队；避免停止后仍念出已生成内容。
  if (!force && isLiveAutoAct(data) && !liveAutoAllowed()) return;
  if (!force && isLiveAutoAct(data) && data.liveGen != null && speech.liveGen != null
      && Number(data.liveGen) !== Number(speech.liveGen)) return;
  enqueueSpeech(data);
}

function adoptLiveSession(info) {
  if (!info || typeof info !== "object") return;
  if (typeof info.liveSession === "boolean") speech.liveSession = info.liveSession;
  if (info.liveGen != null && Number.isFinite(Number(info.liveGen))) {
    speech.liveGen = Number(info.liveGen);
  }
  // 桌宠 / 全屏画面跟随服务端开播：停播就立刻闭嘴并跳过缓存句。
  if (window.chachaDesktopPet || window.chachaStageCast) {
    const on = !!speech.liveSession;
    if (speech.live !== on) {
      speech.live = on;
      if (!on) {
        stopSpeech();
        syncCueCursor();
      }
    }
  }
}

/** 当前这一边收不收音。桌宠开着时只让桌宠听，直播间只跟着开关。 */
function micShouldListen() {
  if (window.chachaDesktopPet) return share.owner === "pet";
  return share.owner !== "pet";
}

function micSettings() {
  return { want: !!mic.want, hold: mic.hold.slice(0, 2) };
}

function noteMic() {
  if (share.quiet || window.chachaStageCast) return;
  const body = micSettings();
  body.owner = window.chachaDesktopPet ? "pet" : "web";
  fetch("/api/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mic: body }),
  }).catch(() => {});
}

function lookBody() {
  const view = window.stage && window.stage.getView ? window.stage.getView() : { zoom: 1, x: 0, y: 0 };
  const petBox = $("pet-scale");
  const zoom = window.chachaDesktopPet ? Number(petBox && petBox.value) || 1 : view.zoom;
  const toggles = {};
  ["physics", "breath", "sway", "blink", "mouse", "ahoge"].forEach((name) => {
    const box = $("opt-" + name);
    toggles[name] = !!(box && box.checked);
  });
  const pose = {};
  for (const item of params) {
    if (!locks.has(item.index)) continue;
    pose[item.id] = Number(locks.get(item.index));
  }
  return {
    modelId: currentModel ? currentModel.id : "",
    zoom: Math.round(zoom * 1000) / 1000,
    x: Math.round((window.chachaDesktopPet ? share.panX : view.x) * 10) / 10,
    y: Math.round((window.chachaDesktopPet ? share.panY : view.y) * 10) / 10,
    toggles: toggles,
    pose: pose,
  };
}

/** 把这边的形象交给服务。桌宠加载完成前不写，免得盖掉直播间刚记下的样子。 */
function noteLook() {
  // 全屏画面只跟随控制页形象，不回写。
  if (window.chachaStageCast) return;
  if (share.quiet || !share.lookReady || !currentModel) return;
  const body = lookBody();
  const text = JSON.stringify(body);
  if (text === share.lastLook) return;
  share.lastLook = text;
  share.panX = body.x;
  share.panY = body.y;
  fetch("/api/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ look: body }),
  }).catch(() => {});
}

window.stageShare = { note: noteLook };

function applySharedToggles(toggles) {
  const map = {
    physics: "opt-physics",
    breath: "opt-breath",
    sway: "opt-sway",
    blink: "opt-blink",
    mouse: "opt-mouse",
    ahoge: "opt-ahoge",
  };
  Object.keys(map).forEach((key) => {
    const box = $(map[key]);
    if (!box || toggles[key] == null || box.checked === !!toggles[key]) return;
    box.checked = !!toggles[key];
    box.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

function applySharedPose(pose) {
  locks.clear();
  for (const item of params) item.row.classList.remove("locked");
  Object.keys(pose || {}).forEach((id) => {
    const item = byId.get(id);
    if (!item) return;
    const value = clamp(item, pose[id]);
    locks.set(item.index, value);
    item.row.classList.add("locked");
  });
}

async function applyRemoteLook(body) {
  share.quiet = true;
  try {
    share.panX = Number(body.x) || 0;
    share.panY = Number(body.y) || 0;
    if (body.modelId && currentModel && body.modelId !== currentModel.id) {
      const known = catalog.find((item) => item.id === body.modelId && item.ready);
      if (known) await showModel(body.modelId, { keepView: true });
    }
    applySharedToggles(body.toggles || {});
    applySharedPose(body.pose || {});
    if (window.chachaDesktopPet) {
      const scale = Math.min(4, Math.max(0.6, Number(body.zoom) || 1));
      const box = $("pet-scale");
      if (box && Math.abs(Number(box.value) - scale) > 0.02) {
        box.value = String(scale);
        box.dispatchEvent(new Event("input", { bubbles: true }));
      }
    } else if (window.stage && window.stage.setView) {
      const zoom = Math.min(4, Math.max(0.25, Number(body.zoom) || 1));
      const view = window.stage.getView();
      if (Math.abs(view.zoom - zoom) > 0.02 || Math.abs(view.x - share.panX) > 0.5 || Math.abs(view.y - share.panY) > 0.5) {
        window.stage.setView(zoom, share.panX, share.panY);
      }
    }
    share.lookReady = true;
    share.lastLook = JSON.stringify(lookBody());
  } finally {
    share.quiet = false;
  }
}

function applyRemoteMic(data) {
  share.quiet = true;
  share.owner = data.owner || "web";
  try {
    mic.want = !!data.want;
    mic.hold = Array.isArray(data.hold) ? data.hold.filter((key) => validHoldKey(key)).slice(0, 2) : [];
    const box = $("opt-mic");
    if (box) box.checked = mic.want;
    const select = $("mic-device");
    if (select) select.disabled = !mic.want;
    paintMicKey();
    try {
      localStorage.setItem("chacha-mic", JSON.stringify({ deviceId: mic.deviceId || "", hold: mic.hold }));
    } catch (error) {}
  } finally {
    share.quiet = false;
  }
  syncPetTalkKey();
  if (!micShouldListen()) {
    if (mic.holding) finishHold();
    stopRecognition();
    closeMicStream();
    setMicHint(mic.want ? "语音由另一边接收。" : micOffHint());
    return;
  }
  if (mic.want && !mic.opening && !mic.stream && !mic.recognition) setMicEnabled(true).catch(() => {});
  else if (!mic.want) setMicEnabled(false);
  else setMicHint(holdHint());
}

function pollShare() {
  if (share.busy) return;
  share.busy = true;
  fetch("/api/share?look=" + share.lookRev + "&mic=" + share.micRev)
    .then((response) => response.json())
    .then(async (data) => {
      if (typeof data.lookRev === "number") share.lookRev = data.lookRev;
      if (typeof data.micRev === "number") share.micRev = data.micRev;
      if (data.look) await applyRemoteLook(data.look);
      else if (window.chachaDesktopPet && !share.lookReady && !data.lookRev) {
        share.missedLooks = (share.missedLooks || 0) + 1;
        if (share.missedLooks >= 4) share.lookReady = true;
      }
      if (!data.mic) return;
      share.owner = data.mic.owner || share.owner;
      const incoming = JSON.stringify({ want: !!data.mic.want, hold: data.mic.hold || [] });
      if (incoming !== JSON.stringify(micSettings())) {
        applyRemoteMic(data.mic);
        return;
      }
      if (!micShouldListen() && (mic.stream || mic.recognition || mic.holding)) {
        stopRecognition();
        closeMicStream();
        setMicHint(mic.want ? "语音由另一边接收。" : micOffHint());
      } else if (micShouldListen() && mic.want && !mic.opening && !mic.stream && !mic.recognition) {
        setMicEnabled(true).catch(() => {});
      }
    })
    .catch(() => {})
    .finally(() => { share.busy = false; });
}

window.setInterval(pollShare, 400);
if (window.chachaDesktopPet) {
  const beat = () => {
    fetch("/api/share", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mic: { owner: "pet" } }),
    }).catch(() => {});
  };
  beat();
  window.setInterval(beat, 1000);
}

/** 对齐到当前最新口播编号；只接收对齐之后新生成的句子。 */
async function syncCueCursor() {
  try {
    // after 取超大，pending 为空，服务端会回 idle + 当前 actId。
    const data = await (await fetch("/api/cue?after=2147483647")).json();
    const head = Number(data && data.actId);
    if (Number.isFinite(head) && head >= 0) {
      speech.actId = head;
      speech.seenActs.add(head);
    }
  } catch (error) {
    // 对齐失败也放行轮询，最多漏掉旧缓存里的一部分。
  } finally {
    speech.cueReady = true;
  }
}
syncCueCursor();

window.setInterval(() => {
  if (!speech.cueReady || speech.cueBusy) return;
  speech.cueBusy = true;
  fetch("/api/cue?after=" + (speech.actId || 0))
    .then((response) => response.json())
    .then((data) => deliverSpeech(data))
    .catch(() => {})
    .finally(() => { speech.cueBusy = false; });
}, 400);

/** 播队列里的一句。说完再叫下一句，表情从上一句的脸上缓过去。 */
function startLine(token, index, reply) {
  pauseMic();
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
  if (window.stageClips) {
    // 口播自动动作：节流；模型自带统一 5 秒后恢复原样。
    window.stageClips.play(line, { auto: true });
  }
  speech.cues = Array.isArray(line.cues) ? line.cues.slice(0, 1) : [];
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
    if (index + 1 >= speech.lines.length) {
      finishSpeech();
      return;
    }
    // 普通弹幕复述完第一句后停顿。礼物和进房不思考。
    const replyKind = speechKind(reply);
    const danmakuThink = replyKind !== "gift" && replyKind !== "enter" && !!(reply && reply.heard) && index === 0;
    const pauseMs = danmakuThink ? 1100 + Math.random() * 700 : 0;
    if (pauseMs > 0) {
      setCue("思考中…");
      $("status").textContent = (reply.heard && reply.heard.uname ? ("回 " + reply.heard.uname + " · ") : "") + "思考中…";
      window.setTimeout(() => {
        if (speech.token !== token) return;
        startLine(token, index + 1, reply);
      }, pauseMs);
      return;
    }
    startLine(token, index + 1, reply);
  };
  if (!line.audio) {
    window.setTimeout(goNext, speech.duration * 1000 + 180);
    return;
  }
  const audio = new Audio(line.audio);
  audio.preload = "auto";
  audio.volume = 1;
  audio.muted = !!speech.muted;
  speech.audio = audio;
  // 播放前解锁自动播放；不再接管 WebAudio，避免静音。
  unlockAudio();
  speech.analyserOn = wireAnalyser(audio);
  audio.onloadedmetadata = () => {
    if (speech.token === token && Number.isFinite(audio.duration) && audio.duration > 0) {
      speech.duration = audio.duration;
    }
  };
  audio.onended = goNext;
  const tryPlay = () => {
    const p = audio.play();
    if (p && typeof p.then === "function") {
      p.catch((error) => {
        speech.notice = "没能播放声音：" + (error.message || "浏览器拦住了");
        $("status").textContent = speech.notice;
        toast(speech.notice);
        // 被拦时仍按预估时长往下走，避免卡死。
        window.setTimeout(goNext, speech.duration * 1000 + 180);
      });
    }
  };
  // 等一点缓冲再播，减少「有请求却没声」。
  if (audio.readyState >= 2) tryPlay();
  else audio.addEventListener("canplay", tryPlay, { once: true });
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

/** streamed 口播只交了编号：立刻把本轮 act 拉下来播，不等 400ms 轮询。 */
async function pullStreamedActs(data, opts) {
  adoptLiveSession(data);
  const force = !!(opts && opts.force);
  // 停播后丢掉自动直播结果，避免还在路上的请求落地后开口。
  if (!force && isLiveAutoAct(data) && !liveAutoAllowed()) return;
  if (!data || !data.streamed) {
    deliverSpeech(data, opts);
    return;
  }
  const ids = (data.actIds || []).map(Number).filter((n) => Number.isFinite(n) && n > 0);
  if (!ids.length) return;
  const after = Math.min(...ids) - 1;
  try {
    const cue = await (await fetch("/api/cue?after=" + after)).json();
    if (!force && !liveAutoAllowed() && isLiveAutoAct(data)) return;
    if (data.filler && cue && !cue.idle) cue.filler = true;
    if (data.heard && cue && !cue.heard) cue.heard = data.heard;
    deliverSpeech(cue, opts);
  } catch (error) {
    console.warn(error);
  }
}

/** 台词 → 贴合表情。闲聊兜底用，避免随机发型/拉琴/话筒。 */
const SPEECH_CLIP_RULES = [
  [/哭|难过|伤心|可怜|呜呜|泪/, ["1哭耳", "1泪1", "1泪2", "2哭嘴"]],
  [/吓|惊吓|吓一跳|突然|哇{2,}/, ["1惊", "1星"]],
  [/惊喜|居然|太棒|厉害/, ["1星", "1惊"]],
  [/气|哼+|讨厌|烦死|生气/, ["1气"]],
  [/晕|搞不懂|什么情况|莫名其妙/, ["1晕"]],
  [/无语|呵呵|汗|离谱/, ["1汗", "1黑"]],
  [/怕|害怕|紧张|慌/, ["1鼻线"]],
  [/喜欢|爱了|心动|爱你/, ["1心眼", "3心"]],
  [/害羞|不好意思|脸红|羞/, ["1红1", "1红2", "3扣手指"]],
  [/可爱|嘿嘿|卖萌/, ["1红1", "1红2", "1星"]],
  [/再见|拜拜|回见|挥手/, ["3挥手", "挥手"]],
  [/手机|刷一下|看看手机/, ["3手机"]],
  [/游戏|开一把|手柄/, ["3手柄"]],
  [/谢谢|感谢|礼物|舰长|提督|总督|送出/, ["谢谢舰长大人爱你们", "1舰长", "1心眼", "1星"]],
  [/欢迎|进来啦|进直播间|光临/, ["挥手", "1星", "1心眼"]],
  [/看看|凑近|前倾|盯/, ["2前倾"]],
  [/小提琴|拉琴|弹琴/, ["3小提琴"]],
  [/唱歌|唱一首|话筒|麦克风/, ["3话筒"]],
];

const SPEECH_CLIP_BY_MOOD = {
  happy: ["1星", "1心眼"],
  shy: ["1红1", "1红2", "3扣手指"],
  angry: ["1气"],
  sad: ["1哭耳", "1泪1", "2哭嘴"],
  surprise: ["1惊", "1星"],
  star: ["1星", "1心眼"],
  think: ["1晕", "1汗"],
  cool: ["1黑", "1汗"],
};

/** 当前模型按钮里确实有的表情 id。 */
function knownFaceIds() {
  const ids = new Set();
  document.querySelectorAll("#clips-faces button").forEach((el) => {
    const id = (el.textContent || "").trim();
    if (id) ids.add(id);
  });
  return ids;
}

/** 按说话内容（再按 mood）选表情；对不上就轻轻害羞。 */
function pickClipForSpeech(text, mood) {
  const known = knownFaceIds();
  const pick = (list) => {
    for (const id of list || []) {
      if (known.size && !known.has(id)) continue;
      return id;
    }
    return null;
  };
  const blob = String(text || "");
  for (const [re, clips] of SPEECH_CLIP_RULES) {
    if (re.test(blob)) {
      const id = pick(clips);
      if (id) return id;
    }
  }
  const moodKey = String(mood || "").trim().toLowerCase();
  const byMood = pick(SPEECH_CLIP_BY_MOOD[moodKey]);
  if (byMood) return byMood;
  return pick(["1红1", "1星"]) || null;
}

function noteSpoke() {
  speech.lastSpokeAt = performance.now();
}

/** 空闲或正在说的时候预取下一条。生成结果只排队，不打断当前这句。 */
async function armLive() {
  // 等 cue 游标对齐完成，避免刷新瞬间生成的新句被 sync 跳过，或旧缓存被重播。
  if (!speech.cueReady || !speech.live || speech.fetching || speech.queue.length) return;
  if (speech.busy && !speech.active) return;
  speech.fetching = true;
  let tryFiller = false;
  try {
    const data = await postJson("/api/chat", {
      live: true,
      lang: $("talk-lang").value,
      history: speech.history,
      freedom: window.stageFreedom ? window.stageFreedom.enabled() : false,
      model: window.stage && window.stage.current && window.stage.current() ? window.stage.current().id : "",
      ...voiceBody(),
    });
    adoptLiveSession(data);
    showDanmaku(data.danmaku);
    if (!speech.live) return;
    if (data.idle) {
      // 没有弹幕：松开 fetching 后再尝试冷场闲聊（弹幕后静默窗内只继续探弹幕）。
      tryFiller = true;
      return;
    }
    rememberTurn(data);
    await pullStreamedActs(data);
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
  if (tryFiller && speech.live) {
    const quietLeft = Number(speech.postDanmakuQuietUntil) - performance.now();
    if (quietLeft > 0) {
      window.setTimeout(() => { if (speech.live) armLive(); }, Math.min(quietLeft, 1000));
      return;
    }
    armFiller();
  }
}

/** 约 10 秒没开口就自己聊事迹/热点/歌曲，保持直播不冷场。弹幕优先。 */
async function armFiller() {
  if (!speech.cueReady || !speech.live || speech.fetching || speech.fillerBusy) return;
  if (speech.active || speech.queue.length || speech.busy) return;
  // 刚回完弹幕：先等 10 秒看还有没有弹幕，不要续刚才的闲聊。
  if (performance.now() < Number(speech.postDanmakuQuietUntil || 0)) return;
  const gap = 8000 + Math.random() * 4000;
  const last = Number(speech.lastSpokeAt) || 0;
  if (performance.now() - last < gap) return;
  speech.fillerBusy = true;
  speech.fetching = true;
  setCue("闲聊构思中…");
  try {
    const data = await postJson("/api/chat", {
      live: true,
      filler: true,
      lang: $("talk-lang").value,
      history: speech.history,
      freedom: window.stageFreedom ? window.stageFreedom.enabled() : false,
      model: window.stage && window.stage.current && window.stage.current() ? window.stage.current().id : "",
      ...voiceBody(),
    });
    adoptLiveSession(data);
    if (!speech.live) {
      setCue("");
      return;
    }
    if (data.danmaku) showDanmaku(data.danmaku);
    if (data.idle) {
      setCue("");
      return;
    }
    // 若生成期间来了弹幕，服务端仍可能返回弹幕轮；按普通回复处理。
    rememberTurn(data);
    data.filler = data.filler === true || (!data.heard && !!data.say);
    // 先记下开口时间，避免构思时又连发；再立刻拉本轮 cue 开播。
    noteSpoke();
    await pullStreamedActs(data);
    if (speech.live && data.filler) $("status").textContent = "闲聊防冷场中";
  } catch (error) {
    console.warn(error);
    setCue("");
  } finally {
    speech.fetching = false;
    window.setTimeout(() => { speech.fillerBusy = false; }, 2000);
  }
}

// 供调试与防缓存丢函数：挂到 window。
window.armFiller = armFiller;
window.armLive = armLive;

// 语音输入默认关闭。只听用户选中的那只麦克风。
// 没设按键时一直听；设了按键就按住才录，松开发给 dsh。按键最多两个。
const HOLD_LABELS = {
  Control: "Ctrl", Shift: "Shift", Alt: "Alt", Meta: "Win", Space: "空格",
  Enter: "回车", Tab: "Tab", Escape: "Esc", Backspace: "退格", Delete: "Delete",
  ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→",
};
const mic = {
  want: false,
  deviceId: "",
  hold: [],
  capturing: false,
  holding: false,
  holdFinishing: false,
  heard: "",
  interim: "",
  captureDown: [],
  capturePeak: [],
  pageHeld: new Set(),
  stream: null,
  opening: false,
  recognition: null,
  lastText: "",
  lastAt: 0,
  // 按住期间识别失败会重试。主动松开不算中断。
  failCount: 0,
};

function setMicHint(text) {
  const hint = $("mic-state");
  if (hint) hint.textContent = text;
}

function formatHold(keys) {
  return (keys || []).map((key) => HOLD_LABELS[key] || key).join(" + ");
}

function validHoldKey(name) {
  if (/^[A-Z]$/.test(name) || /^[0-9]$/.test(name) || /^F([1-9]|1[0-2])$/.test(name)) return true;
  return Object.prototype.hasOwnProperty.call(HOLD_LABELS, name);
}

function validHold(list) {
  if (!Array.isArray(list)) return [];
  const keys = [];
  list.slice(0, 2).forEach((item) => {
    const name = String(item || "");
    if (validHoldKey(name) && !keys.includes(name)) keys.push(name);
  });
  return keys;
}

function keyName(event) {
  const code = event.code || "";
  if (code.startsWith("Control")) return "Control";
  if (code.startsWith("Shift")) return "Shift";
  if (code.startsWith("Alt")) return "Alt";
  if (code.startsWith("Meta")) return "Meta";
  if (code === "Space") return "Space";
  if (code === "Enter" || code === "NumpadEnter") return "Enter";
  if (code === "Tab") return "Tab";
  if (code === "Escape") return "Escape";
  if (code === "Backspace") return "Backspace";
  if (code === "Delete") return "Delete";
  if (/^F([1-9]|1[0-2])$/.test(code)) return code;
  if (/^Key[A-Z]$/.test(code)) return code.slice(3);
  if (/^Digit[0-9]$/.test(code)) return code.slice(5);
  if (/^Arrow(Up|Down|Left|Right)$/.test(code)) return code;
  return "";
}

function holdHint() {
  const name = micName();
  if (mic.hold.length) return "按住「" + formatHold(mic.hold) + "」说话，松开后发给 dsh。";
  return "正在听「" + name + "」。说完一句就会发给 dsh。";
}

function micOffHint() {
  if (mic.hold.length) return "语音输入关着。打开后，按住「" + formatHold(mic.hold) + "」才会录音。";
  return "语音输入关着。打开后可选一只麦克风，也可以设置按住说话的按键。";
}

function micName() {
  const select = $("mic-device");
  return select && select.selectedOptions[0] ? select.selectedOptions[0].textContent : "麦克风";
}

function writeMic() {
  try {
    localStorage.setItem("chacha-mic", JSON.stringify({
      deviceId: mic.deviceId || "",
      hold: mic.hold,
    }));
  } catch (error) {}
  noteMic();
}

function readMic() {
  try {
    const saved = JSON.parse(localStorage.getItem("chacha-mic") || "{}");
    return {
      deviceId: saved && saved.deviceId ? String(saved.deviceId) : "",
      hold: validHold(saved && saved.hold),
    };
  } catch (error) {
    return { deviceId: "", hold: [] };
  }
}

function paintMicKey() {
  const button = $("btn-mic-key");
  const clear = $("btn-mic-key-clear");
  if (!button) return;
  button.setAttribute("aria-pressed", mic.capturing ? "true" : "false");
  button.textContent = mic.capturing ? "请按下按键…" : (mic.hold.length ? "按住 " + formatHold(mic.hold) : "设置按住说话");
  if (clear) clear.disabled = mic.capturing || !mic.hold.length;
}

/** 调桌宠窗口上的接口。页面刚打开时接口可能还没挂上，等它就绪再补一次。 */
function callPetApi(method, arg) {
  if (!window.chachaDesktopPet) return;
  const send = () => {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[method] !== "function") return false;
    api[method](arg);
    return true;
  };
  if (send()) return;
  window.addEventListener("pywebviewready", send, { once: true });
}

/** 把按键交给桌宠进程。浮窗不在前台时，页面自己收不到键盘。 */
function syncPetTalkKey() {
  const active = mic.want && !mic.capturing ? mic.hold.join(",") : "";
  callPetApi("set_talk_key", active);
}

/** 登记快捷键时让桌宠全局钩子把键名送回来，规则和直播间页面一致。 */
function syncPetCapture(on) {
  callPetApi("set_talk_capture", !!on);
}

function recognitionLang() {
  const lang = $("talk-lang") && $("talk-lang").value;
  if (lang === "ja") return "ja-JP";
  if (lang === "en") return "en-US";
  return "zh-CN";
}

/** 列出音频输入。拿到权限之后才会有设备名字。 */
async function listMics() {
  const select = $("mic-device");
  if (!select || !navigator.mediaDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter((device) => device.kind === "audioinput");
  const current = select.value || mic.deviceId;
  select.replaceChildren();
  if (!inputs.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有麦克风";
    select.appendChild(option);
    return;
  }
  inputs.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || ("麦克风 " + (index + 1));
    select.appendChild(option);
  });
  const preferred = [...select.options].some((option) => option.value === current) ? current : inputs[0].deviceId;
  select.value = preferred;
  mic.deviceId = select.value;
  writeMic();
}

function closeMicStream() {
  if (!mic.stream) return;
  mic.stream.getTracks().forEach((track) => track.stop());
  mic.stream = null;
}

/** 打开选中的麦克风。没有指定时用系统默认的那只。 */
async function openMic() {
  closeMicStream();
  const id = $("mic-device") && $("mic-device").value;
  const audio = {
    echoCancellation: true,
    noiseSuppression: true,
  };
  if (id) audio.deviceId = { exact: id };
  mic.stream = await navigator.mediaDevices.getUserMedia({ audio });
  const track = mic.stream.getAudioTracks()[0];
  if (track && track.getSettings) {
    const settings = track.getSettings();
    if (settings.deviceId) mic.deviceId = settings.deviceId;
  }
  await listMics();
}

function stopRecognition() {
  const recognition = mic.recognition;
  mic.recognition = null;
  if (!recognition) return;
  recognition.onend = null;
  recognition.onresult = null;
  recognition.onerror = null;
  try { recognition.abort(); } catch (error) {}
}

function pauseMic() {
  stopRecognition();
}

function resumeMic() {
  if (!mic.want || speech.active || speech.busy || speech.queue.length) return;
  if (mic.hold.length) {
    if (mic.holding) startRecognition(true);
    return;
  }
  if (mic.recognition) return;
  startRecognition(false);
}

function submitHeard(text) {
  if (!mic.want || speech.busy || speech.active) return;
  const ask = $("btn-ask");
  const box = $("ask-text");
  if (!ask || !box) return;
  pauseMic();
  box.value = text;
  ask.click();
}

function startRecognition(hold) {
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Ctor) {
    setMicHint("这个窗口没有语音识别。请用 Chrome 或 Edge 打开直播间。");
    return false;
  }
  stopRecognition();
  const recognition = new Ctor();
  recognition.lang = recognitionLang();
  recognition.continuous = true;
  recognition.interimResults = !!hold;
  try {
    if (mic.stream) recognition.mediaStream = mic.stream;
  } catch (error) {}
  recognition.onresult = (event) => {
    mic.failCount = 0;
    let text = "";
    let interim = "";
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const piece = event.results[index][0].transcript;
      if (event.results[index].isFinal) text += piece;
      else interim += piece;
    }
    if (hold) {
      if (!mic.holding && !mic.holdFinishing) return;
      if (text.trim()) mic.heard = (mic.heard + " " + text).trim();
      mic.interim = interim.trim();
      const live = (mic.heard + " " + mic.interim).trim();
      if (live) setMicHint("正在听：" + live);
      return;
    }
    text = text.trim();
    if (!text || !mic.want) return;
    const now = performance.now();
    if (text === mic.lastText && now - mic.lastAt < 4000) return;
    mic.lastText = text;
    mic.lastAt = now;
    submitHeard(text);
  };
  recognition.onerror = (event) => {
    const code = event.error || "";
    if (code === "no-speech" || code === "aborted" || mic.holdFinishing) return;
    if (code === "audio-capture") closeMicStream();
    if (code === "not-allowed" || code === "service-not-allowed") {
      const box = $("opt-mic");
      mic.want = false;
      mic.holding = false;
      if (box) box.checked = false;
      const select = $("mic-device");
      if (select) select.disabled = true;
      closeMicStream();
      syncPetTalkKey();
      setMicHint("麦克风权限被拒绝了。");
      return;
    }
    if (mic.holding && (code === "network" || code === "audio-capture")) {
      mic.failCount += 1;
      if (mic.failCount < 3) return;
      setMicHint(code === "network" ? "语音识别连不上。松手后再按一次。" : "麦克风被占用了。松手后再按一次。");
      return;
    }
    setMicHint("语音识别中断：" + code);
  };
  recognition.onend = () => {
    if (hold) {
      if (mic.holdFinishing) return;
      if (!mic.holding || !mic.want || mic.recognition !== recognition) return;
      window.setTimeout(() => {
        if (!mic.holding || !mic.want || mic.recognition !== recognition) return;
        try { recognition.mediaStream = null; } catch (error) {}
        try { recognition.start(); } catch (error) {}
      }, 250);
      return;
    }
    if (!mic.want || speech.active || speech.busy || mic.recognition !== recognition) return;
    window.setTimeout(() => {
      if (!mic.want || speech.active || speech.busy || mic.recognition !== recognition) return;
      try { recognition.start(); } catch (error) {}
    }, 250);
  };
  mic.recognition = recognition;
  try {
    recognition.start();
    return true;
  } catch (error) {
    mic.recognition = null;
    setMicHint(error.message || "语音识别没有启动");
    return false;
  }
}

function flushHold() {
  const text = ((mic.heard || "") + " " + (mic.interim || "")).trim();
  mic.heard = "";
  mic.interim = "";
  mic.holding = false;
  mic.holdFinishing = false;
  if (text) submitHeard(text);
  else if (mic.want) setMicHint(holdHint());
}

function finishHold() {
  mic.holding = false;
  const recognition = mic.recognition;
  if (!recognition) {
    flushHold();
    return;
  }
  if (mic.holdFinishing) return;
  mic.holdFinishing = true;
  recognition.onerror = () => {};
  recognition.onend = () => {
    if (mic.recognition === recognition) mic.recognition = null;
    flushHold();
  };
  try {
    recognition.stop();
  } catch (error) {
    stopRecognition();
    flushHold();
    return;
  }
  window.setTimeout(() => {
    if (!mic.holdFinishing) return;
    stopRecognition();
    flushHold();
  }, 700);
}

/** 按住说话。桌宠的全局按键和页面自己的键盘都会调到这里。 */
window.micHold = (down) => {
  if (!micShouldListen()) return;
  if (!mic.want || !mic.hold.length || mic.capturing) return;
  if (down) {
    if (mic.holding || mic.holdFinishing || speech.active || speech.busy || speech.queue.length) return;
    mic.holding = true;
    mic.heard = "";
    mic.interim = "";
    mic.failCount = 0;
    if (!startRecognition(true)) {
      mic.holding = false;
      return;
    }
    setMicHint("正在录音…松开后发给 dsh。");
    return;
  }
  if (!mic.holding && !mic.recognition) return;
  finishHold();
};

async function setMicEnabled(on) {
  const select = $("mic-device");
  if (on && mic.opening) return;
  mic.want = !!on;
  if (select) select.disabled = !mic.want;
  noteMic();
  if (!mic.want) {
    mic.holding = false;
    mic.holdFinishing = false;
    mic.heard = "";
    mic.interim = "";
    stopRecognition();
    closeMicStream();
    syncPetTalkKey();
    setMicHint(micOffHint());
    return;
  }
  if (!micShouldListen()) {
    stopRecognition();
    closeMicStream();
    syncPetTalkKey();
    setMicHint("语音由另一边接收。");
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    mic.want = false;
    const box = $("opt-mic");
    if (box) box.checked = false;
    if (select) select.disabled = true;
    noteMic();
    setMicHint("这个窗口用不了麦克风。");
    return;
  }
  setMicHint("正在打开麦克风…");
  mic.opening = true;
  try {
    await openMic();
    if (!mic.want) {
      closeMicStream();
      return;
    }
    syncPetTalkKey();
    if (mic.hold.length) {
      setMicHint(holdHint());
      return;
    }
    const started = startRecognition(false);
    if (!started) return;
    setMicHint(holdHint());
  } catch (error) {
    mic.want = false;
    const box = $("opt-mic");
    if (box) box.checked = false;
    if (select) select.disabled = true;
    closeMicStream();
    syncPetTalkKey();
    const denied = error && (error.name === "NotAllowedError" || error.name === "SecurityError");
    setMicHint(denied ? "麦克风权限被拒绝了。" : (error.message || "麦克风打不开"));
  } finally {
    mic.opening = false;
  }
}

function cancelMicCapture() {
  mic.capturing = false;
  mic.captureDown = [];
  mic.capturePeak = [];
  paintMicKey();
  syncPetCapture(false);
  syncPetTalkKey();
  if (mic.want) setMicHint(holdHint());
  else setMicHint(micOffHint());
}

function beginMicCapture() {
  mic.capturing = true;
  mic.holding = false;
  mic.holdFinishing = false;
  mic.heard = "";
  mic.interim = "";
  mic.captureDown = [];
  mic.capturePeak = [];
  stopRecognition();
  paintMicKey();
  syncPetTalkKey();
  syncPetCapture(true);
  setMicHint("按下要使用的按键，松开后保存。最多两个键。按 Esc 取消。");
}

/** 直播间和桌宠共用这一套：按下记入组合，全部松开后保存，单独 Esc 取消。 */
function noteCaptureKey(name, down) {
  if (!mic.capturing || !validHoldKey(name)) return;
  if (down) {
    if (mic.captureDown.includes(name)) return;
    if (mic.capturePeak.length >= 2) return;
    mic.captureDown.push(name);
    mic.capturePeak.push(name);
    setMicHint("已按下 " + formatHold(mic.capturePeak) + "。松开后保存，Esc 取消。");
    return;
  }
  mic.captureDown = mic.captureDown.filter((key) => key !== name);
  if (!mic.captureDown.length && mic.capturePeak.length) finishMicCapture();
}

window.micCaptureKey = (name, down) => {
  noteCaptureKey(String(name || ""), !!down);
};

function finishMicCapture() {
  const keys = mic.capturePeak.slice(0, 2);
  mic.capturing = false;
  mic.captureDown = [];
  mic.capturePeak = [];
  const cancelled = keys.length === 1 && keys[0] === "Escape";
  if (!cancelled) mic.hold = keys;
  paintMicKey();
  if (mic.want) setMicHint(holdHint());
  else setMicHint(micOffHint());
  // 钩子通知页面的那一轮里不能再回调桌宠进程，否则两边互相等待。
  window.setTimeout(() => {
    syncPetCapture(false);
    syncPetTalkKey();
    if (!cancelled) writeMic();
  }, 0);
}

function onMicKeyDown(event) {
  // 桌宠的录音只跟全局钩子走。页面自己再收一次，会刚开始就被松开打断。
  if (window.chachaDesktopPet) return;
  const name = keyName(event);
  if (!name) return;
  if (mic.capturing) {
    event.preventDefault();
    event.stopPropagation();
    if (event.repeat) return;
    noteCaptureKey(name, true);
    return;
  }
  if (!micShouldListen() || !mic.hold.length) return;
  if (event.repeat) return;
  mic.pageHeld.add(name);
  if (mic.hold.every((key) => mic.pageHeld.has(key))) {
    event.preventDefault();
    window.micHold(true);
  }
}

function onMicKeyUp(event) {
  if (window.chachaDesktopPet) return;
  const name = keyName(event);
  if (!name) return;
  if (mic.capturing) {
    event.preventDefault();
    noteCaptureKey(name, false);
    return;
  }
  if (!mic.pageHeld.has(name)) return;
  mic.pageHeld.delete(name);
  if (mic.hold.length && !mic.hold.every((key) => mic.pageHeld.has(key))) window.micHold(false);
}

function bindMic() {
  const box = $("opt-mic");
  const select = $("mic-device");
  const keyButton = $("btn-mic-key");
  const clearButton = $("btn-mic-key-clear");
  if (!box || !select) return;
  box.checked = false;
  select.disabled = true;
  const saved = readMic();
  mic.deviceId = saved.deviceId;
  mic.hold = saved.hold;
  paintMicKey();
  setMicHint(micOffHint());
  if (!window.chachaDesktopPet) noteMic();
  box.addEventListener("change", () => {
    setMicEnabled(box.checked);
  });
  select.addEventListener("change", () => {
    mic.deviceId = select.value;
    writeMic();
    if (!mic.want || !micShouldListen()) return;
    openMic().then(() => {
      if (!mic.want) return;
      if (mic.hold.length) {
        if (mic.holding) startRecognition(true);
        else setMicHint(holdHint());
        return;
      }
      startRecognition(false);
      setMicHint(holdHint());
    }).catch((error) => {
      setMicHint(error.message || "这只麦克风打不开");
    });
  });
  if (keyButton) keyButton.addEventListener("click", () => {
    if (mic.capturing) cancelMicCapture();
    else beginMicCapture();
  });
  if (clearButton) clearButton.addEventListener("click", () => {
    mic.hold = [];
    mic.pageHeld.clear();
    mic.holding = false;
    mic.holdFinishing = false;
    mic.heard = "";
    mic.interim = "";
    stopRecognition();
    writeMic();
    paintMicKey();
    syncPetTalkKey();
    if (mic.want && micShouldListen()) {
      startRecognition(false);
      setMicHint(holdHint());
    } else setMicHint(micOffHint());
  });
  window.addEventListener("keydown", onMicKeyDown, true);
  window.addEventListener("keyup", onMicKeyUp, true);
  window.addEventListener("blur", () => {
    mic.pageHeld.clear();
    // 桌宠经常不在前台，失焦不能把正在登记的按键清掉。
    if (window.chachaDesktopPet) return;
    if (mic.capturing) cancelMicCapture();
    else window.micHold(false);
  });
  window.addEventListener("storage", (event) => {
    if (event.key !== "chacha-mic" || mic.capturing) return;
    const saved = readMic();
    if (saved.hold.join(",") === mic.hold.join(",") && saved.deviceId === mic.deviceId) return;
    mic.deviceId = saved.deviceId;
    mic.hold = saved.hold;
    const select = $("mic-device");
    if (select && [...select.options].some((option) => option.value === saved.deviceId)) {
      select.value = saved.deviceId;
    }
    paintMicKey();
    syncPetTalkKey();
    if (!mic.holding) setMicHint(mic.want ? holdHint() : micOffHint());
  });
  const lang = $("talk-lang");
  if (lang) {
    lang.addEventListener("change", () => {
      if (!mic.want) return;
      if (mic.hold.length) {
        if (mic.holding) startRecognition(true);
        return;
      }
      startRecognition(false);
    });
  }
  if (navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", () => {
      if (mic.want) listMics().catch(() => {});
    });
  }
  syncPetTalkKey();
}

function bindTalk() {
  bindMic();
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
      deliverSpeech(data, { force: true });
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
    $("status").textContent = "dsh 正在写，写完一句就念…";
    setCue("dsh 正在写，写完一句就念…");
    try {
      const data = await postJson("/api/chat", {
        text,
        lang: $("talk-lang").value,
        history: speech.history,
        freedom: window.stageFreedom ? window.stageFreedom.enabled() : false,
      model: window.stage && window.stage.current && window.stage.current() ? window.stage.current().id : "",
        ...voiceBody(),
      });
      rememberTurn(data, text);
      $("talk-text").value = data.say || "";
      $("ask-text").value = "";
      await pullStreamedActs(data, { force: true });
      if (!data.streamed) deliverSpeech(data, { force: true });
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
  bindSession();
  bindAudioMute();
  bindClearVisits();
  bindStageFullscreen();
  bindStageCastLinks();
  bindStageCastPage();
  window.setInterval(() => {
    // 全屏画面定期拉状态，跟随控制页开始/停止。
    if (window.chachaStageCast) {
      refreshDanmaku();
      if (speech.live) pumpSpeech();
      return;
    }
    if (!speech.live) return;
    pumpSpeech();
    if (!window.chachaDesktopPet) {
      armLive();
      refreshDanmaku();
    }
  }, 1500);
  // 独立闲聊时钟：不依赖「弹幕空转」分支，避免旧缓存或抢锁导致永远不聊。
  window.setInterval(() => {
    if (!speech.live) return;
    if (window.chachaStageCast || window.chachaDesktopPet) return;
    armFiller();
  }, 2000);
}

/** 同步顶栏按钮与「接弹幕」勾选时置位，避免 checked 赋值再触发 change。 */
let sessionSyncing = false;

/** 顶栏「开始 / 停止」：一键开全套（形象开关 + 待机 + 接弹幕口播）。 */
function paintSession() {
  const btn = $("btn-session");
  const on = !!speech.live && !window.chachaDesktopPet && !window.chachaStageCast;
  if (btn) {
    btn.textContent = on ? "停止" : "开始";
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.hidden = !!(window.chachaDesktopPet || window.chachaStageCast);
  }
  const copyBtn = $("btn-stage-copy");
  if (copyBtn) copyBtn.hidden = !!(window.chachaDesktopPet || window.chachaStageCast);
  const fullBtn = $("btn-stage-full");
  if (fullBtn) fullBtn.hidden = !!(window.chachaDesktopPet || window.chachaStageCast);
  const fullFloat = $("btn-stage-full-float");
  if (fullFloat) fullFloat.hidden = !!(window.chachaDesktopPet || window.chachaStageCast);
  const live = $("opt-live");
  if (live && live.checked !== on) {
    sessionSyncing = true;
    live.checked = on;
    sessionSyncing = false;
  }
}

function ensureAvatarBasics() {
  // 开播时把常用形象开关拉起来，避免忘开眨眼/物理。
  ["opt-physics", "opt-breath", "opt-blink"].forEach((id) => {
    const el = $(id);
    if (el) el.checked = true;
  });
  if (typeof applyRuntimeToggles === "function" && model) applyRuntimeToggles();
}

function playSessionIntro() {
  // 只回待机，开场不强制挥手，避免动作太密。
  if (!window.stage) return;
  try {
    if (typeof window.stage.motion === "function") window.stage.motion("Idle");
  } catch (error) {}
}

/** 丢掉刷新前积压的待回弹幕，只接这之后的新内容。 */
async function markDanmakuFresh() {
  try {
    const data = await postJson("/api/danmaku/fresh", {});
    if (data && data.danmaku) showDanmaku(data.danmaku);
  } catch (error) {}
}

async function startSession() {
  if (window.chachaDesktopPet || window.chachaStageCast) {
    toast("请在控制页点「开始」；本页只显示画面");
    return;
  }
  if (!model) {
    toast("先加载模型");
    return;
  }
  let status = null;
  try {
    status = await (await fetch("/api/status")).json();
  } catch (error) {
    toast("读不到服务状态");
    return;
  }
  if (status && status.llmReady === false) {
    toast("先在「连接」里配好 dsh / DeepSeek 密钥");
    return;
  }
  unlockAudio();
  ensureAvatarBasics();
  // 开播当刻起算：旧弹幕不回，历史也不带去复读。
  speech.history = [];
  stopSpeech();
  speech.liveError = "";
  speech.fillerBusy = false;
  speech.fetching = false;
  speech.postDanmakuQuietUntil = 0;
  speech.spokeDanmaku = false;
  noteSpoke();
  // 先通知服务端开播，之后才允许弹幕/找话题。
  try {
    const session = await postJson("/api/session", { live: true });
    adoptLiveSession(session);
    if (session.danmaku) showDanmaku(session.danmaku);
  } catch (error) {
    toast(error.message || "没能开启直播会话");
    return;
  }
  speech.live = true;
  paintSession();
  playSessionIntro();
  await syncCueCursor();
  await markDanmakuFresh();
  armLive();
  refreshDanmaku();
  showDanmaku(status && status.danmaku ? status.danmaku : { connected: false });
  $("status").textContent = "已开播 · 接弹幕 · 找话题闲聊";
  setCue("全套流程已开启");
  toast("已开始：接弹幕，空闲约10秒会自己找话题");
}

async function stopSession() {
  speech.live = false;
  speech.liveSession = false;
  speech.fetching = false;
  speech.fillerBusy = false;
  speech.postDanmakuQuietUntil = 0;
  speech.spokeDanmaku = false;
  stopSpeech();
  if (window.stageClips && typeof window.stageClips.restoreClip === "function") {
    window.stageClips.restoreClip();
  } else if (window.stageClips && typeof window.stageClips.clearHand === "function") {
    window.stageClips.clearHand();
  }
  // 通知服务端停播：作废进行中的直播生成，并丢掉待回弹幕。
  try {
    const session = await postJson("/api/session", { live: false });
    adoptLiveSession(session);
    if (session.danmaku) showDanmaku(session.danmaku);
  } catch (error) {
    console.warn(error);
  }
  await syncCueCursor();
  paintSession();
  showDanmaku({ connected: false, board: speech.board || [], hint: "已停止：不再找话题/回弹幕" });
  $("status").textContent = "已停止";
  setCue("");
  toast("已停止：找话题、回弹幕、口播全部停下");
}

function bindSession() {
  const btn = $("btn-session");
  const live = $("opt-live");
  paintSession();
  if (btn) {
    btn.addEventListener("click", () => {
      if (speech.live) stopSession().catch((error) => toast(error.message || "没能停止"));
      else startSession().catch((error) => toast(error.message || "没能开始"));
    });
  }
  if (live) {
    live.addEventListener("change", () => {
      if (sessionSyncing) return;
      if (live.checked) startSession().catch((error) => toast(error.message || "没能开始"));
      else stopSession().catch((error) => toast(error.message || "没能停止"));
    });
  }
}

/** 清除进房 uname 记录，之后再进都按第一次欢迎。 */
function bindClearVisits() {
  const btn = $("btn-clear-visits");
  if (!btn || window.chachaStageCast || window.chachaPet) return;
  btn.addEventListener("click", async () => {
    if (!window.confirm("清除全部进房记录？之后观众再进都会当第一次欢迎。")) return;
    btn.disabled = true;
    try {
      const data = await postJson("/api/visits/clear", {});
      const n = Number(data && data.cleared) || 0;
      if (data && data.danmaku) showDanmaku(data.danmaku);
      toast(n ? ("已清除 " + n + " 人的进房记录") : "进房记录已是空的");
    } catch (error) {
      toast(error.message || "清除失败");
    } finally {
      btn.disabled = false;
    }
  });
}

/** 口播静音开关：立刻作用到正在播的句子，并记住偏好。 */
function audioMuteKey() {
  return window.chachaStageCast ? "chacha-audio-muted-stage" : "chacha-audio-muted";
}

function paintAudioMute() {
  const btn = $("btn-audio-mute");
  if (!btn) return;
  const on = !!speech.muted;
  btn.textContent = on ? "已静音" : "声音开";
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("is-muted", on);
  btn.title = on ? "点击开启口播声音" : "点击关闭口播声音";
}

function setAudioMuted(muted) {
  speech.muted = !!muted;
  try {
    localStorage.setItem(audioMuteKey(), speech.muted ? "1" : "0");
  } catch (error) {}
  if (speech.audio) {
    try { speech.audio.muted = speech.muted; } catch (error) {}
  }
  paintAudioMute();
}

function bindAudioMute() {
  let saved = "0";
  try { saved = localStorage.getItem(audioMuteKey()) || "0"; } catch (error) { saved = "0"; }
  speech.muted = saved === "1";
  paintAudioMute();
  const btn = $("btn-audio-mute");
  if (!btn) return;
  // 全屏画面不要声音按钮；桌宠也不显示。
  btn.hidden = !!(window.chachaDesktopPet || window.chachaStageCast);
  if (btn.hidden) return;
  btn.addEventListener("click", () => {
    unlockAudio();
    setAudioMuted(!speech.muted);
    toast(speech.muted ? "口播已静音" : "口播声音已开");
  });
}

/** 控制页：把直播画面（舞台+字幕）拉进浏览器全屏。 */
function stageFullscreenElement() {
  return document.fullscreenElement || document.webkitFullscreenElement || null;
}

function paintStageFullscreen() {
  const on = stageFullscreenElement() === $("stage-column");
  ["btn-stage-full", "btn-stage-full-float"].forEach((id) => {
    const btn = $(id);
    if (!btn) return;
    btn.textContent = on ? "退出全屏" : "全屏";
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.classList.toggle("is-on", on);
  });
}

function toggleStageFullscreen() {
  const column = $("stage-column");
  if (!column) return;
  const active = stageFullscreenElement();
  if (active) {
    const exit = document.exitFullscreen || document.webkitExitFullscreen;
    if (exit) Promise.resolve(exit.call(document)).catch(() => {});
    return;
  }
  unlockAudio();
  const req = column.requestFullscreen || column.webkitRequestFullscreen;
  if (!req) {
    toast("当前浏览器不支持全屏");
    return;
  }
  Promise.resolve(req.call(column)).catch((error) => {
    toast(error && error.message ? error.message : "没能进入全屏");
  });
}

function bindStageFullscreen() {
  if (window.chachaDesktopPet || window.chachaStageCast) return;
  ["btn-stage-full", "btn-stage-full-float"].forEach((id) => {
    const btn = $(id);
    if (btn) btn.addEventListener("click", toggleStageFullscreen);
  });
  document.addEventListener("fullscreenchange", paintStageFullscreen);
  document.addEventListener("webkitfullscreenchange", paintStageFullscreen);
  paintStageFullscreen();
}

/** 全屏纯画面链接（另一个网址，只显示直播舞台）。 */
function stageCastUrl() {
  const url = new URL(location.origin + "/");
  url.searchParams.set("surface", "stage");
  return url.toString();
}

async function copyStageCastUrl() {
  const url = stageCastUrl();
  const field = $("stage-cast-url");
  if (field) field.value = url;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(url);
    } else if (field) {
      field.focus();
      field.select();
      document.execCommand("copy");
    } else {
      throw new Error("no clipboard");
    }
    toast("已复制全屏画面链接");
    return true;
  } catch (error) {
    toast("复制失败，请手动复制：" + url);
    return false;
  }
}

function bindStageCastLinks() {
  if (window.chachaStageCast || window.chachaDesktopPet) return;
  const field = $("stage-cast-url");
  const url = stageCastUrl();
  if (field) field.value = url;
  const openBtn = $("btn-stage-open");
  if (openBtn) {
    openBtn.addEventListener("click", () => {
      window.open(stageCastUrl(), "chacha-stage-cast", "noopener,noreferrer");
    });
  }
  ["btn-stage-copy", "btn-stage-copy-dock"].forEach((id) => {
    const btn = $(id);
    if (btn) btn.addEventListener("click", () => { copyStageCastUrl(); });
  });
}

/** 纯画面页：点击进入浏览器全屏，并解锁声音；不发起找话题。 */
function bindStageCastPage() {
  if (!window.chachaStageCast) return;
  document.body.classList.add("stage-cast");
  let tip = $("stage-cast-tip");
  if (!tip) {
    tip = document.createElement("button");
    tip.type = "button";
    tip.id = "stage-cast-tip";
    tip.textContent = "点击进入全屏";
    document.body.appendChild(tip);
  }
  const goFull = () => {
    unlockAudio();
    const root = document.documentElement;
    const req = root.requestFullscreen || root.webkitRequestFullscreen || root.msRequestFullscreen;
    if (req) Promise.resolve(req.call(root)).catch(() => {});
    tip.hidden = true;
  };
  tip.addEventListener("click", goFull);
  document.addEventListener("keydown", (event) => {
    if (event.key === "f" || event.key === "F") goFull();
  });
  speech.fetching = false;
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
  document.addEventListener("chacha-hud-refresh", refreshHud);
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

/** 只允许 http(s) 头像，避免脏 URL 进 img。 */
function safeUface(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (raw.indexOf("http://") === 0 || raw.indexOf("https://") === 0) return raw;
  if (raw.indexOf("//") === 0) return "https:" + raw;
  return "";
}

/** 舞台上的气泡。粉色可爱风；头像优先用开放平台 uface。 */
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
    const face = safeUface(item.uface);
    const ava = face
      ? ("<img src=\"" + escapeText(face) + "\" alt=\"\" loading=\"lazy\" referrerpolicy=\"no-referrer\" onerror=\"this.remove()\" />" +
         "<span class=\"dm-ava-fallback\">" + escapeText(mark) + "</span>")
      : escapeText(mark);
    return (
      "<article class=\"dm dm-" + state + (fresh ? " dm-in" : "") + "\">" +
      "<div class=\"dm-ava\">" + ava + "</div>" +
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
    adoptLiveSession(status);
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
