/* 舞台模式、背景、自由操控，以及模型自带动作。控件表 FREEDOM_CONTROLS 与 /schema/engine.md 对应。 */
(function () {
  const STORE = "engine-ui";
  // 再小就点不中角色，只能重启。桌宠缩放下限停在这里。
  const PET_SCALE_MIN = 0.6;
  const PET_SCALE_MAX = 4;
  // 自由操控可开关的功能。新增一项时同步改 engine_model.TOGGLE_IDS 和 schema。
  const FREEDOM_CONTROLS = [
    { id: "hud", label: "血条", box: "hud-show" },
    { id: "danmaku", label: "弹幕", box: "dm-show" },
    { id: "physics", label: "物理", box: "opt-physics" },
    { id: "breath", label: "呼吸", box: "opt-breath" },
    { id: "sway", label: "摆头", box: "opt-sway" },
    { id: "blink", label: "眨眼", box: "opt-blink" },
    { id: "mouse", label: "鼠标注视", box: "opt-mouse" },
    { id: "live", label: "接弹幕", box: "opt-live" },
    { id: "captions", label: "字幕" },
  ];
  let imageUrl = "";
  let petPointer = null;
  let petDrag = null;
  // 模型自带：3* 为手部（同时只能一个）；所有自带 clip 显示 5 秒后恢复原样。
  const CLIP_HOLD_MS = 5000;
  let activeHandExpression = "";
  let clipPlayedMotion = false;
  let clipRestoreTimer = 0;
  // 3挥手待机是叠加参数，清表情层后手不会自己放下。到点前不要被别的恢复打断。
  let waveHoldUntil = 0;
  // 挥手相关参数：默认手保持模型默认值，其余手势收到 0。
  const WAVE_HAND_IDS = [
    "Param8", "Param10", "Param50", "Param58", "Param128",
    "Param89", "Param90", "Param127", "Param169", "Param133",
    "Param136", "Param170", "Param172", "Param179", "Param182", "Param88",
  ];
  const WAVE_GESTURE_IDS = {
    Param50: 0, Param58: 0, Param128: 0, Param89: 0, Param90: 0,
    Param127: 0, Param169: 0, Param133: 0, Param136: 0, Param170: 0,
    Param172: 0, Param179: 0, Param182: 0, Param88: 0,
  };
  // 口播驱动的动作节流，避免句句换动作。
  let lastAutoClipAt = 0;

  function $(id) { return document.getElementById(id); }

  /** 手部动作：模型自带里以 3 开头的都是手部/道具。 */
  function isHandClip(id) {
    return String(id || "").charAt(0) === "3";
  }

  /** 挥手 / 3挥手 / 3挥手待机：一律只亮 5 秒，然后强制回默认手。 */
  function isWaveHold(id) {
    return String(id || "").indexOf("挥手") >= 0;
  }

  function coreModel() {
    const model = window.stageModel;
    return model && model.internalModel ? model.internalModel.coreModel : null;
  }

  function parameterIndex(core, id) {
    try {
      if (typeof core.getParameterIndex === "function") return core.getParameterIndex(id);
    } catch (error) {}
    return -1;
  }

  // Idle 不覆盖挥手参数；必须在 afterMotionUpdate（saveParameters 之前）压回，才会被存住。
  let handHoldUntil = 0;
  let handHoldHandler = null;
  let handHoldModel = null;
  const HAND_HOLD_MS = 1800;

  /** 挥手用过的手部参数收回模型默认值；挥手手势明确归零。 */
  function defaultHandPose(persist) {
    const core = coreModel();
    if (!core) return;
    WAVE_HAND_IDS.forEach((id) => {
      const index = parameterIndex(core, id);
      if (index < 0) return;
      let value = 0;
      if (Object.prototype.hasOwnProperty.call(WAVE_GESTURE_IDS, id)) {
        value = WAVE_GESTURE_IDS[id];
      } else {
        try {
          if (typeof core.getParameterDefaultValueByIndex === "function") {
            value = core.getParameterDefaultValueByIndex(index);
          }
        } catch (error) {}
      }
      try { core.setParameterValueByIndex(index, value); } catch (error) {}
    });
    // 立刻写入参数快照，避免下一帧 loadParameters 又读回挥手姿势。
    if (persist !== false) {
      try {
        if (typeof core.saveParameters === "function") core.saveParameters();
      } catch (error) {}
    }
  }

  /** 立刻停掉表情队列，避免 1 秒淡出期间继续 Add 挥手。 */
  function stopExpressionsHard() {
    const model = window.stageModel;
    const mgr = expressionManager(model);
    if (!mgr) return;
    try {
      if (typeof mgr.stopAllExpressions === "function") mgr.stopAllExpressions();
    } catch (error) {}
    try {
      if (typeof mgr.resetExpression === "function") mgr.resetExpression();
    } catch (error) {}
  }

  function clearHandHold() {
    handHoldUntil = 0;
    if (handHoldHandler && handHoldModel) {
      try { handHoldModel.off("afterMotionUpdate", handHoldHandler); } catch (error) {}
    }
    handHoldHandler = null;
    handHoldModel = null;
  }

  /** 在 saveParameters 前持续压回默认手；挥手显示期内不抢跑。 */
  function holdDefaultHands() {
    clearHandHold();
    handHoldUntil = performance.now() + HAND_HOLD_MS;
    const model = window.stageModel;
    const internal = model && model.internalModel;
    if (!internal || typeof internal.on !== "function") {
      defaultHandPose(true);
      return;
    }
    handHoldModel = internal;
    handHoldHandler = () => {
      if (waveHoldUntil && performance.now() < waveHoldUntil) return;
      if (performance.now() >= handHoldUntil) {
        clearHandHold();
        defaultHandPose(true);
        return;
      }
      // 不在这里 save：本帧随后会 saveParameters，正好把默认手存进去。
      defaultHandPose(false);
    };
    internal.on("afterMotionUpdate", handHoldHandler);
    defaultHandPose(true);
  }

  function expressionManager(model) {
    return model && model.internalModel && model.internalModel.motionManager
      ? model.internalModel.motionManager.expressionManager
      : null;
  }

  function paintHandButtons() {
    const faces = $("clips-faces");
    if (!faces) return;
    faces.querySelectorAll("button[data-hand='1']").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.clipId === activeHandExpression);
    });
  }

  /** 恢复模型自带：清表情，非待机动作回到 Idle，手部高亮取消。force 为真时立刻收回挥手。 */
  function restoreClip(force) {
    if (!force && waveHoldUntil && performance.now() < waveHoldUntil) {
      scheduleClipRestore(waveHoldUntil - performance.now());
      return;
    }
    if (clipRestoreTimer) {
      window.clearTimeout(clipRestoreTimer);
      clipRestoreTimer = 0;
    }
    // 挥手.motion 会 Loop；Idle 不带手部曲线。表情挥手是 Add。都要压回默认手。
    const needDefaultHands = waveHoldUntil > 0 || isWaveHold(activeHandExpression) || clipPlayedMotion;
    waveHoldUntil = 0;
    const model = window.stageModel;
    stopExpressionsHard();
    if (needDefaultHands && model && typeof model.motion === "function") {
      try {
        const motions = model.internalModel && model.internalModel.motionManager;
        if (motions && typeof motions.stopAllMotions === "function") motions.stopAllMotions();
      } catch (error) {}
      try { model.motion("Idle", 0, 3); } catch (error) {}
    }
    if (needDefaultHands) {
      defaultHandPose();
      holdDefaultHands();
    }
    activeHandExpression = "";
    clipPlayedMotion = false;
    paintHandButtons();
  }

  /** 兼容旧调用名。 */
  function clearHand() {
    restoreClip();
  }

  /** 所有自带 clip 统一 5 秒后恢复原样。挥手待机按剩余时间计。 */
  function scheduleClipRestore(delay) {
    if (clipRestoreTimer) window.clearTimeout(clipRestoreTimer);
    const wait = Math.max(0, Number(delay) || CLIP_HOLD_MS);
    clipRestoreTimer = window.setTimeout(() => {
      clipRestoreTimer = 0;
      restoreClip();
    }, wait);
  }

  function isDesktopPet() {
    return !!window.chachaDesktopPet;
  }

  function currentMode() {
    if (isDesktopPet()) return "pet";
    return $("mode-pet") && $("mode-pet").getAttribute("aria-pressed") === "true" ? "pet" : "live";
  }

  function readStore() {
    try { return JSON.parse(localStorage.getItem(STORE) || "{}"); }
    catch (error) { return {}; }
  }

  function writeStore(extra) {
    const prev = readStore();
    const next = Object.assign({}, prev, extra || {}, {
      mode: currentMode(),
      liveTab: ($("dock") && $("dock").getAttribute("data-live")) || "talk",
      freedom: $("opt-freedom").checked,
      color: $("bg-color").value,
      imageScale: Number($("bg-scale").value),
      imageOpacity: Number($("bg-opacity").value),
      hudZ: Number($("hud-z").value),
      dmZ: Number($("dm-z").value),
      petScale: Number($("pet-scale").value),
      css: $("user-css").value,
      captions: !document.body.classList.contains("hide-caption"),
    });
    if (imageUrl && imageUrl.length < 1500000) next.image = imageUrl;
    else delete next.image;
    localStorage.setItem(STORE, JSON.stringify(next));
  }

  function clamp(value, low, high, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(high, Math.max(low, number));
  }

  function safeCss(text) {
    const raw = String(text || "").slice(0, 6000);
    const lowered = raw.toLowerCase();
    const blocked = ["</", "@import", "javascript:", "expression(", "behavior:", "-moz-binding", "@charset"];
    if (blocked.some((token) => lowered.indexOf(token) >= 0)) return "";
    return raw;
  }

  function paintBackdrop() {
    const stage = $("stage");
    const bg = $("stage-bg");
    if (!stage || !bg) return;
    const pet = document.body.classList.contains("mode-pet");
    stage.style.background = pet ? "transparent" : ($("bg-color").value || "#ff00ff");
    bg.style.opacity = String(clamp($("bg-opacity").value, 0, 1, 1));
    bg.style.backgroundSize = (clamp($("bg-scale").value, 0.2, 3, 1) * 100) + "% auto";
    bg.style.backgroundImage = imageUrl ? "url(\"" + imageUrl.replace(/"/g, "") + "\")" : "none";
  }

  function paintLayers() {
    const hud = $("hud");
    const dm = $("danmaku-layer");
    if (hud) hud.style.zIndex = String(clamp($("hud-z").value, 1, 40, 3));
    if (dm) dm.style.zIndex = String(clamp($("dm-z").value, 1, 40, 4));
  }

  function setBox(id, on) {
    const box = $(id);
    if (!box || box.checked === on) return;
    box.checked = on;
    box.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function applyToggle(id, on) {
    if (id === "captions") {
      document.body.classList.toggle("hide-caption", !on);
      return;
    }
    const item = FREEDOM_CONTROLS.find((entry) => entry.id === id);
    if (item && item.box) setBox(item.box, on);
  }

  let petResizeWaiting = false;
  let petResizeWanted = 1;

  /** 桌宠倍率改的是窗口。模型始终铺满新画布，不再在旧画布里被放大后裁掉。 */
  function resizePetWindow(scale) {
    petResizeWanted = clamp(scale, PET_SCALE_MIN, PET_SCALE_MAX, 1);
    const run = () => {
      const api = window.pywebview && window.pywebview.api;
      if (!api || !api.resize_pet) return;
      api.resize_pet(petResizeWanted);
      [160, 480, 900].forEach((delay) => {
        window.setTimeout(() => {
          if (window.stage && window.stage.layout) window.stage.layout();
        }, delay);
      });
    };
    if (window.pywebview && window.pywebview.api) {
      run();
      return;
    }
    if (petResizeWaiting) return;
    petResizeWaiting = true;
    window.addEventListener("pywebviewready", () => {
      petResizeWaiting = false;
      run();
    }, { once: true });
  }

  function setModel(scale, x, y) {
    if (isDesktopPet()) {
      const value = clamp(scale, PET_SCALE_MIN, PET_SCALE_MAX, 1);
      const box = $("pet-scale");
      if (box && Math.abs(Number(box.value) - value) > 0.001) box.value = String(value);
      if (window.stage && window.stage.setView) window.stage.setView(1, 0, 0);
      resizePetWindow(value);
      if (window.stageShare) window.stageShare.note();
      return;
    }
    if (!window.stage || !window.stage.setView) return;
    window.stage.setView(
      clamp(scale, 0.25, 4, 1),
      clamp(x, -2000, 2000, 0),
      clamp(y, -2000, 2000, 0),
    );
    if (window.stageShare) window.stageShare.note();
  }

  function applyCss(text) {
    let tag = document.getElementById("engine-user-css");
    if (!tag) {
      tag = document.createElement("style");
      tag.id = "engine-user-css";
      document.head.appendChild(tag);
    }
    tag.textContent = safeCss(text);
  }

  /** 自由操控的舞台指令。关着的时候调用方不应进来。换模型要等加载完再调位置。 */
  async function applyStage(stage) {
    if (!stage || typeof stage !== "object") return;
    if (!$("opt-freedom").checked) return;
    if (stage.modelId && window.stage && window.stage.use) {
      const rows = window.stageLibrary || [];
      const allowed = rows.some((item) => item.id === stage.modelId && item.freedom && item.ready);
      if (allowed) await window.stage.use(stage.modelId);
    }
    if (stage.mode === "pet") setMode("pet");
    if (stage.mode === "live") setMode("live");
    if (stage.model) {
      const current = window.stage && window.stage.getView ? window.stage.getView() : { zoom: 1, x: 0, y: 0 };
      const scale = stage.model.scale == null ? current.zoom : stage.model.scale;
      const x = stage.model.x == null ? current.x : stage.model.x;
      const y = stage.model.y == null ? current.y : stage.model.y;
      setModel(scale, x, y);
      $("free-scale").value = String(scale);
      $("free-x").value = String(x);
      $("free-y").value = String(y);
    }
    if (stage.background) {
      if (stage.background.color) $("bg-color").value = stage.background.color;
      if (stage.background.imageOpacity != null) $("bg-opacity").value = String(stage.background.imageOpacity);
      if (stage.background.imageScale != null) $("bg-scale").value = String(stage.background.imageScale);
      paintBackdrop();
    }
    const widgets = stage.widgets || {};
    if (widgets.hud) {
      if (typeof widgets.hud.show === "boolean") applyToggle("hud", widgets.hud.show);
      if (widgets.hud.z != null) $("hud-z").value = String(widgets.hud.z);
      moveWidget("hud", widgets.hud);
      if (widgets.hud.style) setBoxValue("hud-style", widgets.hud.style);
      if (widgets.hud.scale != null) setRange("hud-scale", widgets.hud.scale);
    }
    if (widgets.danmaku) {
      if (typeof widgets.danmaku.show === "boolean") applyToggle("danmaku", widgets.danmaku.show);
      if (widgets.danmaku.z != null) $("dm-z").value = String(widgets.danmaku.z);
      moveWidget("danmaku-layer", widgets.danmaku);
      if (widgets.danmaku.scale != null) setRange("dm-scale", widgets.danmaku.scale);
      if (widgets.danmaku.count != null) setRange("dm-count", widgets.danmaku.count);
    }
    if (stage.toggles) {
      Object.keys(stage.toggles).forEach((id) => {
        if (typeof stage.toggles[id] === "boolean") applyToggle(id, stage.toggles[id]);
      });
    }
    if (typeof stage.css === "string") {
      $("user-css").value = safeCss(stage.css);
      applyCss($("user-css").value);
    }
    paintLayers();
    writeStore();
  }

  function setBoxValue(id, value) {
    const el = $(id);
    if (!el) return;
    el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function setRange(id, value) {
    const el = $(id);
    if (!el) return;
    el.value = String(value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function moveWidget(id, spec) {
    const el = $(id);
    if (!el || !spec) return;
    if (spec.x != null) el.style.left = clamp(spec.x, 0, 4000, 16) + "px";
    if (spec.y != null) el.style.top = clamp(spec.y, 0, 4000, 16) + "px";
  }

  /** 直播间留在浏览器里。桌宠另开透明置顶窗口。浮窗里的「回到直播间」只关这扇窗口。 */
  function postPet(open) {
    return fetch("/api/pet", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ open: open }),
    }).then((response) => response.json()).then((data) => {
      if (open && data && data.open === false) {
        $("mode-pet").setAttribute("aria-pressed", "false");
        $("mode-live").setAttribute("aria-pressed", "true");
        writeStore();
        if (data.error) {
          const status = $("status");
          if (status) status.textContent = data.error;
        }
      }
      return data;
    }).catch(() => null);
  }

  function setMode(mode) {
    const pet = mode === "pet";
    if (isDesktopPet()) {
      document.body.classList.add("mode-pet");
      if (!pet) postPet(false);
      paintBackdrop();
      return;
    }
    $("mode-live").setAttribute("aria-pressed", pet ? "false" : "true");
    $("mode-pet").setAttribute("aria-pressed", pet ? "true" : "false");
    paintBackdrop();
    writeStore();
    if (window.stageShare) window.stageShare.note();
    postPet(pet);
  }

  function hitsModel(event) {
    const model = window.stageModel;
    const view = document.querySelector("#stage canvas");
    if (!model || !view || !window.PIXI) return false;
    const rect = view.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < 0 || y < 0 || x > rect.width || y > rect.height) return false;
    try {
      return model.containsPoint(new PIXI.Point(x, y));
    } catch (error) {
      return false;
    }
  }

  /**
   * 播放模型自带动作/表情。
   * options.auto：口播自动触发（会节流）。
   * options.hold：长持，不自动 5 秒收回。
   * 默认播放后 5 秒恢复原样。
   */
  function playClip(line, options) {
    const model = window.stageModel;
    if (!model || !line) return;
    const opts = options || {};
    const auto = !!opts.auto;
    const hold = !!opts.hold;
    const now = performance.now();
    if (auto && (line.motion || line.expression)) {
      // 自动口播：约 6 秒内不连着换 clip。
      if (now - lastAutoClipAt < 6000 && !opts.force) return;
      lastAutoClipAt = now;
    }
    let played = false;
    const waveHold = isWaveHold(line.expression) || isWaveHold(line.motion);
    if (line.expression && typeof model.expression === "function") {
      try {
        const id = String(line.expression);
        const mgr = expressionManager(model);
        if (isHandClip(id)) {
          // 手动再点同一个手部 → 立刻收回。唱歌 hold 时不走切换收回。
          if (!auto && !hold && activeHandExpression === id) {
            restoreClip(true);
            return;
          }
          // 3* 手部一次只能一个：先清再挂。
          if (mgr && typeof mgr.resetExpression === "function") mgr.resetExpression();
          model.expression(id);
          activeHandExpression = id;
          paintHandButtons();
        } else {
          // 非手部表情也会盖掉当前表情层（含手部）。
          if (mgr && typeof mgr.resetExpression === "function") mgr.resetExpression();
          model.expression(id);
          activeHandExpression = "";
          paintHandButtons();
        }
        played = true;
      } catch (error) { console.warn(error); }
    }
    if (line.motion && typeof model.motion === "function") {
      try {
        const motionId = String(line.motion);
        if (isHandClip(motionId) && activeHandExpression) {
          // 手部动作与手部表情互斥。
          const mgr = expressionManager(model);
          if (mgr && typeof mgr.resetExpression === "function") mgr.resetExpression();
          activeHandExpression = "";
          paintHandButtons();
        }
        model.motion(motionId, 0, 3);
        // Idle 本身就是原样，不必再排恢复回 Idle。挥手待机也要在 5 秒后收回。
        if (motionId !== "Idle" && motionId.toLowerCase() !== "idle") {
          clipPlayedMotion = true;
          played = true;
        }
      } catch (error) { console.warn(error); }
    }
    if (played) {
      // 新动作开始时，停掉上一轮压手，避免和当前姿势打架。
      clearHandHold();
      if (hold) {
        if (clipRestoreTimer) {
          window.clearTimeout(clipRestoreTimer);
          clipRestoreTimer = 0;
        }
        waveHoldUntil = 0;
      } else {
        if (waveHold) waveHoldUntil = performance.now() + CLIP_HOLD_MS;
        scheduleClipRestore(waveHold ? CLIP_HOLD_MS : undefined);
      }
    }
  }

  function button(label, onClick) {
    const el = document.createElement("button");
    el.type = "button";
    el.textContent = label;
    el.addEventListener("click", onClick);
    return el;
  }

  function renderClips(catalog) {
    const motions = $("clips-motions");
    const faces = $("clips-faces");
    motions.textContent = "";
    faces.textContent = "";
    if (clipRestoreTimer) {
      window.clearTimeout(clipRestoreTimer);
      clipRestoreTimer = 0;
    }
    activeHandExpression = "";
    clipPlayedMotion = false;
    waveHoldUntil = 0;
    clearHandHold();
    (catalog.motions || []).forEach((item) => {
      const id = item.id;
      const el = button(item.label || id, () => playClip({ motion: id }));
      // 悬停显示实际用途（来自模型 clips.meta.json）。
      if (item.use) el.title = item.use;
      if (isHandClip(id)) {
        el.dataset.hand = "1";
        el.dataset.clipId = id;
      }
      motions.appendChild(el);
    });
    (catalog.expressions || []).forEach((item) => {
      const id = item.id;
      const el = button(item.label || id, () => playClip({ expression: id }));
      if (item.use) el.title = item.use;
      if (isHandClip(id)) {
        el.dataset.hand = "1";
        el.dataset.clipId = id;
      }
      faces.appendChild(el);
    });
    paintHandButtons();
    const swatches = $("bg-swatches");
    swatches.textContent = "";
    (catalog.backdropPresets || []).forEach((item) => {
      const chip = button(item.label, () => {
        $("bg-color").value = item.color;
        paintBackdrop();
        writeStore();
      });
      chip.title = item.label;
      chip.style.background = item.color;
      chip.textContent = "";
      chip.setAttribute("aria-label", item.label);
      swatches.appendChild(chip);
    });
  }

  function renderFreedom() {
    const host = $("freedom-toggles");
    host.textContent = "";
    FREEDOM_CONTROLS.forEach((item) => {
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.freedom = item.id;
      if (item.box && $(item.box)) input.checked = $(item.box).checked;
      if (item.id === "captions") input.checked = !document.body.classList.contains("hide-caption");
      input.addEventListener("change", () => applyToggle(item.id, input.checked));
      label.appendChild(input);
      label.appendChild(document.createTextNode(" " + item.label));
      host.appendChild(label);
    });
  }

  function syncFreedomBoxes() {
    FREEDOM_CONTROLS.forEach((item) => {
      const proxy = document.querySelector("[data-freedom='" + item.id + "']");
      if (!proxy) return;
      if (item.box && $(item.box)) proxy.checked = $(item.box).checked;
      if (item.id === "captions") proxy.checked = !document.body.classList.contains("hide-caption");
    });
  }

  async function loadLink() {
    try {
      const status = await (await fetch("/api/dsh")).json();
      if (status.home) $("dsh-home").value = status.home;
      const hint = $("dsh-hint");
      hint.textContent = status.keyReady
        ? "密钥已写在 dsh 里，这里不显示。血条按官方余额，100 元满血。"
        : (status.hint || "请填上你自己的 DeepSeek 密钥。");
    } catch (error) {
      $("dsh-hint").textContent = "还没连上本机引擎。";
    }
  }

  function restore() {
    const saved = readStore();
    if (saved.color) $("bg-color").value = saved.color;
    if (saved.imageScale) $("bg-scale").value = saved.imageScale;
    if (saved.imageOpacity != null) $("bg-opacity").value = saved.imageOpacity;
    if (saved.hudZ) $("hud-z").value = saved.hudZ;
    if (saved.dmZ) $("dm-z").value = saved.dmZ;
    if (saved.petScale) {
      $("pet-scale").value = String(Math.max(PET_SCALE_MIN, Math.min(PET_SCALE_MAX, Number(saved.petScale) || 1)));
    }
    if (typeof saved.css === "string") $("user-css").value = saved.css;
    if (saved.image) imageUrl = saved.image;
    if (saved.captions === false) document.body.classList.add("hide-caption");
    $("opt-freedom").checked = !!saved.freedom;
    $("freedom-panel").hidden = !$("opt-freedom").checked;
    paintBackdrop();
    paintLayers();
    applyCss($("user-css").value);
    if (saved.liveTab) setLiveTab(saved.liveTab);
    if (saved.mode === "pet") setMode("pet");
  }

  /** 直播间右侧按功能分页。桌宠仍用自己的四张卡片。 */
  function setLiveTab(name) {
    const dock = $("dock");
    const tabs = $("live-tabs");
    if (!dock || !tabs) return;
    const known = ["talk", "scene", "look", "model", "link", "free", "param"];
    const next = known.indexOf(name) >= 0 ? name : "talk";
    dock.setAttribute("data-live", next);
    tabs.querySelectorAll("button").forEach((item) => {
      item.setAttribute("aria-pressed", item.getAttribute("data-live") === next ? "true" : "false");
    });
  }

  let pendingModel = null;

  function renderModelLibrary() {
    const list = $("model-library-list");
    const button = $("btn-browse-model");
    if (button && window.stagePicker) button.textContent = window.stagePicker;
    if (!list) return;
    const rows = window.stageLibrary || [];
    list.replaceChildren();
    rows.forEach((item) => {
      const li = document.createElement("li");
      const title = document.createElement("strong");
      title.textContent = item.name;
      const path = document.createElement("p");
      path.className = "model-path";
      path.textContent = item.path || "";
      const row = document.createElement("div");
      row.className = "row-actions";
      const allow = document.createElement("label");
      allow.className = "check";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = !!item.freedom;
      box.addEventListener("change", async () => {
        try {
          await postModel("/api/models/freedom", { id: item.id, freedom: box.checked });
          if (window.refreshModels) await window.refreshModels();
        } catch (error) {
          box.checked = !box.checked;
          const hint = $("model-library-hint");
          if (hint) hint.textContent = error.message || "开关没有保存";
        }
      });
      allow.appendChild(box);
      allow.appendChild(document.createTextNode(" 允许高自由度使用"));
      const use = document.createElement("button");
      use.type = "button";
      use.textContent = "使用";
      use.disabled = !item.ready;
      use.addEventListener("click", () => {
        if (window.stage && window.stage.use) window.stage.use(item.id);
      });
      row.appendChild(allow);
      row.appendChild(use);
      if (!item.builtin) {
        const drop = document.createElement("button");
        drop.type = "button";
        drop.textContent = "移除";
        drop.addEventListener("click", async () => {
          const data = await postModel("/api/models/remove", { id: item.id });
          if (data && window.refreshModels) await window.refreshModels();
        });
        row.appendChild(drop);
      }
      li.appendChild(title);
      li.appendChild(path);
      li.appendChild(row);
      list.appendChild(li);
    });
  }

  async function postModel(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "模型库没有保存");
    return data;
  }

  function bind() {
    $("mode-live").addEventListener("click", () => setMode("live"));
    $("mode-pet").addEventListener("click", () => setMode("pet"));
    $("btn-back-live").addEventListener("click", () => setMode("live"));
    $("btn-browse-model").addEventListener("click", async () => {
      const button = $("btn-browse-model");
      const hint = $("model-library-hint");
      button.disabled = true;
      hint.textContent = "正在打开选择窗口…";
      try {
        const data = await postModel("/api/models/browse", {});
        if (data.cancelled) {
          hint.textContent = "没有选择文件。";
          return;
        }
        if (data.error) {
          hint.textContent = data.error;
          return;
        }
        pendingModel = data.preview;
        $("model-save-name").value = data.preview.name || "";
        $("model-save-freedom").checked = false;
        $("model-save").hidden = false;
        hint.textContent = "选好了。保存后会出现在模型库。默认不允许高自由度使用。";
      } catch (error) {
        hint.textContent = error.message || "没有打开选择窗口";
      } finally {
        button.disabled = false;
      }
    });
    $("btn-save-model").addEventListener("click", async () => {
      if (!pendingModel) return;
      const hint = $("model-library-hint");
      try {
        const data = await postModel("/api/models/save", {
          path: pendingModel.path,
          name: $("model-save-name").value,
          freedom: $("model-save-freedom").checked,
        });
        pendingModel = null;
        $("model-save").hidden = true;
        if (window.refreshModels) await window.refreshModels();
        if (data.model && window.stage && window.stage.use) await window.stage.use(data.model.id);
        hint.textContent = "已保存。只有勾了高自由度的模型，才会在自由操控打开时允许切换。";
      } catch (error) {
        hint.textContent = error.message || "没有保存";
      }
    });
    $("opt-freedom").addEventListener("change", () => {
      $("freedom-panel").hidden = !$("opt-freedom").checked;
      if ($("opt-freedom").checked) syncFreedomBoxes();
      writeStore();
    });
    ["bg-color", "bg-scale", "bg-opacity", "hud-z", "dm-z", "pet-scale"].forEach((id) => {
      $(id).addEventListener("input", () => {
        paintBackdrop();
        paintLayers();
        if (id === "pet-scale" && document.body.classList.contains("mode-pet")) {
          setModel(Number($("pet-scale").value) || 1, 0, 0);
        }
        writeStore();
      });
    });
    $("bg-file").addEventListener("change", () => {
      const file = $("bg-file").files && $("bg-file").files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        imageUrl = String(reader.result || "");
        paintBackdrop();
        writeStore();
      };
      reader.readAsDataURL(file);
    });
    $("bg-clear").addEventListener("click", () => {
      imageUrl = "";
      $("bg-file").value = "";
      paintBackdrop();
      writeStore();
    });
    $("user-css").addEventListener("input", () => {
      applyCss($("user-css").value);
      writeStore();
    });
    $("free-x").addEventListener("change", () => setModel($("free-scale").value, $("free-x").value, $("free-y").value));
    $("free-y").addEventListener("change", () => setModel($("free-scale").value, $("free-x").value, $("free-y").value));
    $("free-scale").addEventListener("change", () => setModel($("free-scale").value, $("free-x").value, $("free-y").value));
    $("btn-dsh").addEventListener("click", async () => {
      try {
        const data = await (await fetch("/api/dsh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ home: $("dsh-home").value }),
        })).json();
        if (data.error) throw new Error(data.error);
        $("dsh-hint").textContent = data.dsh && data.dsh.keyReady ? "目录已记下。密钥已在 dsh 里。" : "目录已记下。下面还可以填你自己的密钥。";
      } catch (error) {
        $("dsh-hint").textContent = error.message || "目录没有保存";
      }
    });
    $("btn-dsh-key").addEventListener("click", async () => {
      const key = $("dsh-key").value.trim();
      if (!key) {
        $("dsh-hint").textContent = "先填上你自己的 DeepSeek 密钥。";
        return;
      }
      const button = $("btn-dsh-key");
      button.disabled = true;
      try {
        const data = await (await fetch("/api/dsh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ key: key }),
        })).json();
        if (data.error) throw new Error(data.error);
        $("dsh-key").value = "";
        $("dsh-hint").textContent = data.dsh && data.dsh.keyReady
          ? "密钥已写入 dsh 的 .env。血条会按官方余额重算。"
          : "密钥没有保存成功。";
        document.dispatchEvent(new Event("chacha-hud-refresh"));
      } catch (error) {
        $("dsh-hint").textContent = error.message || "密钥没有保存";
      } finally {
        button.disabled = false;
      }
    });
    const stage = $("stage");
    const tabs = $("live-tabs");
    if (tabs) {
      tabs.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button || !tabs.contains(button)) return;
        setLiveTab(button.getAttribute("data-live") || "talk");
        writeStore();
      });
    }
    const cards = $("pet-cards");
    if (cards) {
      cards.addEventListener("click", (event) => {
        const button = event.target.closest("button");
        if (!button || !cards.contains(button)) return;
        const name = button.getAttribute("data-pet-card") || "talk";
        $("dock").setAttribute("data-card", name);
        cards.querySelectorAll("button").forEach((item) => {
          item.setAttribute("aria-pressed", item === button ? "true" : "false");
        });
      });
    }
    window.petHitTest = (x, y) => {
      if (window.petDragging) return true;
      const dock = $("dock");
      if (document.body.classList.contains("pet-open") && dock) {
        const box = dock.getBoundingClientRect();
        if (x >= box.left && y >= box.top && x <= box.right && y <= box.bottom) return true;
      }
      const model = window.stageModel;
      const canvas = document.querySelector("#stage canvas");
      if (!model || !canvas || !window.PIXI) return true;
      const rect = canvas.getBoundingClientRect();
      const px = x - rect.left;
      const py = y - rect.top;
      if (px < 0 || py < 0 || px > rect.width || py > rect.height) return false;
      try {
        return !!model.containsPoint(new PIXI.Point(px, py));
      } catch (error) {
        return false;
      }
    };
    stage.addEventListener("pointerdown", (event) => {
      if (!document.body.classList.contains("mode-pet")) return;
      if (event.target.closest && event.target.closest("#hud, #danmaku-layer, #dock")) return;
      const hit = hitsModel(event);
      petPointer = { x: event.screenX, y: event.screenY, hit: hit, button: event.button };
      if (isDesktopPet() && hit && event.button === 0) {
        window.petDragging = true;
        petDrag = { x: event.screenX, y: event.screenY };
      }
    });
    window.addEventListener("pointermove", (event) => {
      if (!petDrag || !window.pywebview || !window.pywebview.api) return;
      const dx = event.screenX - petDrag.x;
      const dy = event.screenY - petDrag.y;
      if (!dx && !dy) return;
      petDrag.x = event.screenX;
      petDrag.y = event.screenY;
      window.pywebview.api.move_by(dx, dy);
    });
    stage.addEventListener("pointerup", (event) => {
      petPointer = null;
      petDrag = null;
      window.petDragging = false;
    });
    stage.addEventListener("contextmenu", (event) => {
      if (!isDesktopPet()) return;
      event.preventDefault();
      if (!hitsModel(event)) return;
      document.body.classList.toggle("pet-open");
    });
    $("dock").addEventListener("pointerdown", (event) => event.stopPropagation());
    $("dock").addEventListener("contextmenu", (event) => event.preventDefault());
    if (!isDesktopPet()) return;
    const followCursor = () => {
      const api = window.pywebview && window.pywebview.api;
      if (!api || !api.cursor || !api.set_pass) return;
      api.cursor().then((spot) => {
        const hit = spot && window.petHitTest ? window.petHitTest(spot.x, spot.y) : false;
        return api.set_pass(!hit);
      }).catch(() => {});
    };
    let armed = false;
    const arm = () => {
      if (armed) return;
      armed = true;
      window.setInterval(followCursor, 40);
    };
    window.addEventListener("pywebviewready", arm);
    if (window.pywebview && window.pywebview.api) arm();
  }

  window.renderModelLibrary = renderModelLibrary;
  window.stageFreedom = {
    enabled() { return !!($("opt-freedom") && $("opt-freedom").checked); },
    apply: applyStage,
    controls: FREEDOM_CONTROLS.map((item) => item.id),
  };
  window.stageClips = {
    play(line, options) { playClip(line, options); },
    motion(id) { playClip({ motion: id }); },
    expression(id) { playClip({ expression: id }); },
    clearHand,
    restoreClip,
    isHandClip,
    hand() { return activeHandExpression; },
    holdMs: CLIP_HOLD_MS,
  };

  bind();
  if (!isDesktopPet()) {
    window.setInterval(() => {
      fetch("/api/pet").then((response) => response.json()).then((data) => {
        const pressed = $("mode-pet").getAttribute("aria-pressed") === "true";
        if (!data || pressed === !!data.open) return;
        $("mode-pet").setAttribute("aria-pressed", data.open ? "true" : "false");
        $("mode-live").setAttribute("aria-pressed", data.open ? "false" : "true");
        writeStore();
      }).catch(() => {});
    }, 2000);
  }
  renderFreedom();
  restore();
  loadLink();
  fetch("/api/stage").then((response) => response.json()).then(renderClips).catch(() => {});
  window.addEventListener("stage-model", () => {
    if (document.body.classList.contains("mode-pet")) {
      setModel(Number($("pet-scale").value) || 1, 0, 0);
    }
    const current = window.stage && window.stage.current ? window.stage.current() : null;
    const query = current && current.id ? "?model=" + encodeURIComponent(current.id) : "";
    fetch("/api/stage" + query).then((response) => response.json()).then(renderClips).catch(() => {});
  });
})();
