/* 舞台模式、背景、自由操控，以及模型自带动作。控件表 FREEDOM_CONTROLS 与 /schema/engine.md 对应。 */
(function () {
  const STORE = "engine-ui";
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
  let liveView = { zoom: 1, x: 0, y: 0 };
  let petPointer = null;

  function $(id) { return document.getElementById(id); }

  function readStore() {
    try { return JSON.parse(localStorage.getItem(STORE) || "{}"); }
    catch (error) { return {}; }
  }

  function writeStore(extra) {
    const prev = readStore();
    const next = Object.assign({}, prev, extra || {}, {
      mode: document.body.classList.contains("mode-pet") ? "pet" : "live",
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

  function setModel(scale, x, y) {
    if (!window.stage || !window.stage.setView) return;
    window.stage.setView(
      clamp(scale, 0.25, 4, 1),
      clamp(x, -2000, 2000, 0),
      clamp(y, -2000, 2000, 0),
    );
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

  /** 自由操控的舞台指令。关着的时候调用方不应进来。 */
  function applyStage(stage) {
    if (!stage || typeof stage !== "object") return;
    if (!$("opt-freedom").checked) return;
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

  function setMode(mode) {
    const pet = mode === "pet";
    if (pet && !document.body.classList.contains("mode-pet")) {
      liveView = window.stage && window.stage.getView ? window.stage.getView() : liveView;
      document.body.classList.add("mode-pet");
      document.body.classList.remove("pet-open");
      setModel(Number($("pet-scale").value) || 1, 0, 0);
    }
    if (!pet && document.body.classList.contains("mode-pet")) {
      document.body.classList.remove("mode-pet", "pet-open");
      setModel(liveView.zoom, liveView.x, liveView.y);
    }
    $("mode-live").setAttribute("aria-pressed", pet ? "false" : "true");
    $("mode-pet").setAttribute("aria-pressed", pet ? "true" : "false");
    paintBackdrop();
    writeStore();
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

  function playClip(line) {
    const model = window.stageModel;
    if (!model || !line) return;
    if (line.expression && typeof model.expression === "function") {
      try { model.expression(line.expression); } catch (error) { console.warn(error); }
    }
    if (line.motion && typeof model.motion === "function") {
      try { model.motion(line.motion, 0, 3); } catch (error) { console.warn(error); }
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
    (catalog.motions || []).forEach((item) => {
      motions.appendChild(button(item.label || item.id, () => playClip({ motion: item.id })));
    });
    (catalog.expressions || []).forEach((item) => {
      faces.appendChild(button(item.label || item.id, () => playClip({ expression: item.id })));
    });
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
        ? "dsh 已带上密钥。密钥不会显示在这里。"
        : (status.hint || "请在你的 dsh 里配置密钥。");
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
    if (saved.petScale) $("pet-scale").value = saved.petScale;
    if (typeof saved.css === "string") $("user-css").value = saved.css;
    if (saved.image) imageUrl = saved.image;
    if (saved.captions === false) document.body.classList.add("hide-caption");
    $("opt-freedom").checked = !!saved.freedom;
    $("freedom-panel").hidden = !$("opt-freedom").checked;
    paintBackdrop();
    paintLayers();
    applyCss($("user-css").value);
    if (saved.mode === "pet") setMode("pet");
  }

  function bind() {
    $("mode-live").addEventListener("click", () => setMode("live"));
    $("mode-pet").addEventListener("click", () => setMode("pet"));
    $("btn-back-live").addEventListener("click", () => setMode("live"));
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
        $("dsh-hint").textContent = data.dsh && data.dsh.keyReady ? "dsh 已带上密钥。" : "目录已记下。密钥仍放在 dsh 自己的环境里。";
      } catch (error) {
        $("dsh-hint").textContent = error.message || "目录没有保存";
      }
    });
    const stage = $("stage");
    stage.addEventListener("pointerdown", (event) => {
      if (!document.body.classList.contains("mode-pet")) return;
      if (event.target.closest && event.target.closest("#hud, #danmaku-layer, #dock")) return;
      petPointer = { x: event.clientX, y: event.clientY, hit: hitsModel(event) };
    });
    stage.addEventListener("pointerup", (event) => {
      if (!petPointer) return;
      const moved = Math.hypot(event.clientX - petPointer.x, event.clientY - petPointer.y);
      const hit = petPointer.hit;
      petPointer = null;
      if (!document.body.classList.contains("mode-pet") || moved > 6 || !hit) return;
      document.body.classList.toggle("pet-open");
    });
    $("dock").addEventListener("pointerdown", (event) => event.stopPropagation());
  }

  window.stageFreedom = {
    enabled() { return !!($("opt-freedom") && $("opt-freedom").checked); },
    apply: applyStage,
    controls: FREEDOM_CONTROLS.map((item) => item.id),
  };
  window.stageClips = {
    play: playClip,
    motion(id) { playClip({ motion: id }); },
    expression(id) { playClip({ expression: id }); },
  };

  bind();
  renderFreedom();
  restore();
  loadLink();
  fetch("/api/stage").then((response) => response.json()).then(renderClips).catch(() => {});
  window.addEventListener("stage-model", () => {
    if (document.body.classList.contains("mode-pet")) {
      setModel(Number($("pet-scale").value) || 1, 0, 0);
    }
  });
})();
