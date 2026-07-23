(() => {
  "use strict";

  const params = new URLSearchParams(location.search);
  const reviewer = params.get("reviewer") || "hugh";
  const $ = (id) => document.getElementById(id);
  const canvas = $("trajectoryCanvas");
  const ctx = canvas.getContext("2d");

  let reviewIndex = null;
  let labels = {};
  let itemPosition = 0;
  let payload = null;
  let framePosition = 0;
  let labelStart = null;
  let labelEnd = null;
  let playing = false;
  let animationToken = null;
  let playOrigin = 0;
  let elapsedOrigin = 0;
  let view = null;

  const api = (path) => `${path}${path.includes("?") ? "&" : "?"}reviewer=${encodeURIComponent(reviewer)}`;

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
    return body;
  }

  function setMessage(text, kind = "") {
    const node = $("formMessage");
    node.textContent = text;
    node.className = `form-message ${kind}`;
  }

  function selectedValue(name) {
    return document.querySelector(`input[name="${name}"]:checked`)?.value || null;
  }

  function setRadio(name, value) {
    document.querySelectorAll(`input[name="${name}"]`).forEach((input) => {
      input.checked = input.value === value;
    });
  }

  function currentPoint() {
    return payload?.trajectory[framePosition] || null;
  }

  function updateProgress() {
    const total = reviewIndex?.items.length || 0;
    const completed = Object.keys(labels).filter((id) => reviewIndex.items.some((item) => item.item_id === id)).length;
    $("progressText").textContent = `${completed} of ${total} labeled`;
    $("savedText").textContent = completed === total && total ? "Complete" : `${total - completed} remaining`;
    $("progressBar").style.width = total ? `${(completed / total) * 100}%` : "0%";
  }

  function updateBoundaryDisplay() {
    $("startFrame").textContent = labelStart ?? "Not set";
    $("endFrame").textContent = labelEnd ?? "Not set";
    const point = currentPoint();
    $("currentFrame").textContent = point ? point.frame_index : "—";
    draw();
  }

  function updateTimeline() {
    if (!payload) return;
    const point = currentPoint();
    $("timeline").max = String(Math.max(0, payload.trajectory.length - 1));
    $("timeline").value = String(framePosition);
    $("timeOutput").textContent = `${point.elapsed.toFixed(2)} s`;
    updateBoundaryDisplay();
  }

  function eventTypeChanged() {
    const notEvent = selectedValue("event_type") === "not_event";
    $("methodFieldset").disabled = notEvent;
    $("censorFieldset").disabled = notEvent;
    $("setStart").disabled = notEvent;
    $("setEnd").disabled = notEvent;
    $("clearBoundaries").disabled = notEvent;
    if (notEvent) {
      setRadio("method", null);
      setRadio("censoring", null);
      labelStart = null;
      labelEnd = null;
      updateBoundaryDisplay();
    } else {
      if (!selectedValue("method")) setRadio("method", "unclear");
      if (!selectedValue("censoring")) setRadio("censoring", "complete");
    }
  }

  function censoringChanged() {
    const censoring = selectedValue("censoring");
    if (censoring === "left") labelStart = null;
    if (censoring === "right") labelEnd = null;
    if (censoring === "both") {
      labelStart = null;
      labelEnd = null;
    }
    updateBoundaryDisplay();
  }

  function resetForm(saved) {
    setMessage(saved ? `Saved revision ${saved.revision}` : "Not yet labeled");
    if (saved) {
      setRadio("event_type", saved.event_type);
      setRadio("method", saved.method);
      setRadio("censoring", saved.censoring);
      $("exclusionReason").value = saved.exclusion_reason;
      setRadio("confidence", saved.confidence);
      $("note").value = saved.note || "";
      labelStart = saved.start_index;
      labelEnd = saved.end_index;
    } else {
      setRadio("event_type", null);
      setRadio("method", "unclear");
      setRadio("censoring", "complete");
      $("exclusionReason").value = "none";
      setRadio("confidence", "high");
      $("note").value = "";
      labelStart = null;
      labelEnd = null;
    }
    eventTypeChanged();
    updateBoundaryDisplay();
  }

  async function loadItem(position) {
    stopPlayback();
    itemPosition = Math.max(0, Math.min(reviewIndex.items.length - 1, position));
    const item = reviewIndex.items[itemPosition];
    setMessage("Loading trajectory…");
    payload = await fetchJson(api(`/api/item?id=${encodeURIComponent(item.item_id)}`));
    framePosition = 0;
    view = computeView(payload);
    $("itemPosition").textContent = `ITEM ${itemPosition + 1} / ${reviewIndex.items.length}`;
    $("itemTitle").textContent = item.item_id;
    $("provenance").innerHTML = `${payload.scene_id}<br>${payload.vehicle_type} · ${payload.trajectory.length} tracked frames`;
    resetForm(labels[item.item_id]);
    updateTimeline();
  }

  function computeView(item) {
    const xs = item.trajectory.map((p) => p.x);
    const ys = item.trajectory.map((p) => p.y);
    item.stalls.forEach((stall) => {
      xs.push(stall.xmin, stall.xmax);
      ys.push(stall.ymin, stall.ymax);
    });
    let xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
    const width = Math.max(4, xmax - xmin);
    const height = Math.max(4, ymax - ymin);
    const margin = Math.max(2, Math.min(8, Math.max(width, height) * 0.08));
    return { xmin: xmin - margin, xmax: xmax + margin, ymin: ymin - margin, ymax: ymax + margin };
  }

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(1, Math.round(rect.width * dpr));
    const height = Math.max(1, Math.round(rect.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    draw();
  }

  function transform() {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.width / dpr;
    const h = canvas.height / dpr;
    const pad = 25;
    const scale = Math.min((w - pad * 2) / (view.xmax - view.xmin), (h - pad * 2) / (view.ymax - view.ymin));
    const usedW = (view.xmax - view.xmin) * scale;
    const usedH = (view.ymax - view.ymin) * scale;
    const ox = (w - usedW) / 2;
    const oy = (h - usedH) / 2;
    return {
      dpr, w, h, scale,
      point: (x, y) => [ox + (x - view.xmin) * scale, h - oy - (y - view.ymin) * scale],
    };
  }

  function drawPath(points, map, color, width) {
    if (points.length < 2) return;
    ctx.beginPath();
    points.forEach((point, index) => {
      const [x, y] = map.point(point.x, point.y);
      if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.stroke();
  }

  function drawMarker(frameIndex, color, map) {
    if (frameIndex === null) return;
    const point = payload.trajectory.find((p) => p.frame_index === frameIndex);
    if (!point) return;
    const [x, y] = map.point(point.x, point.y);
    ctx.beginPath();
    ctx.arc(x, y, 5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#131316";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  function draw() {
    if (!payload || !view || !canvas.width) return;
    const map = transform();
    ctx.setTransform(map.dpr, 0, 0, map.dpr, 0, 0);
    ctx.clearRect(0, 0, map.w, map.h);
    ctx.fillStyle = "#0e0e10";
    ctx.fillRect(0, 0, map.w, map.h);

    ctx.strokeStyle = "rgba(201,199,192,.28)";
    ctx.lineWidth = 1;
    payload.stalls.forEach((stall) => {
      const [x1, y1] = map.point(stall.xmin, stall.ymin);
      const [x2, y2] = map.point(stall.xmax, stall.ymax);
      ctx.strokeRect(x1, y2, x2 - x1, y1 - y2);
      if (map.scale > 7) {
        ctx.fillStyle = "rgba(201,199,192,.45)";
        ctx.font = "8px Space Grotesk, sans-serif";
        ctx.fillText(stall.stall_id, x1 + 3, y2 + 10);
      }
    });

    drawPath(payload.trajectory, map, "rgba(242,240,234,.30)", 2);
    drawPath(payload.trajectory.slice(0, framePosition + 1), map, "#e8b62b", 3);
    drawMarker(labelStart, "#50c878", map);
    drawMarker(labelEnd, "#ef6a65", map);

    const point = currentPoint();
    if (point) {
      const [x, y] = map.point(point.x, point.y);
      const length = Math.max(8, (payload.vehicle_size[0] || 4.5) * map.scale);
      const width = Math.max(5, (payload.vehicle_size[1] || 2) * map.scale);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(-point.heading);
      ctx.fillStyle = "#f2f0ea";
      ctx.fillRect(-length / 2, -width / 2, length, width);
      ctx.fillStyle = "#131316";
      ctx.fillRect(length * .12, -width * .34, length * .22, width * .68);
      ctx.fillStyle = "#e8b62b";
      ctx.fillRect(length / 2 - 2, -width / 2, 2, width);
      ctx.restore();
    }
  }

  function stopPlayback() {
    playing = false;
    $("playButton").textContent = "PLAY";
    if (animationToken) cancelAnimationFrame(animationToken);
    animationToken = null;
  }

  function animationStep(now) {
    if (!playing || !payload) return;
    const speed = Number($("playbackSpeed").value);
    const target = elapsedOrigin + ((now - playOrigin) / 1000) * speed;
    while (framePosition < payload.trajectory.length - 1 && payload.trajectory[framePosition + 1].elapsed <= target) {
      framePosition += 1;
    }
    updateTimeline();
    if (framePosition >= payload.trajectory.length - 1) stopPlayback();
    else animationToken = requestAnimationFrame(animationStep);
  }

  function togglePlayback() {
    if (!payload) return;
    if (playing) {
      stopPlayback();
      return;
    }
    if (framePosition >= payload.trajectory.length - 1) framePosition = 0;
    playing = true;
    $("playButton").textContent = "PAUSE";
    playOrigin = performance.now();
    elapsedOrigin = currentPoint().elapsed;
    animationToken = requestAnimationFrame(animationStep);
  }

  function step(delta) {
    if (!payload) return;
    stopPlayback();
    framePosition = Math.max(0, Math.min(payload.trajectory.length - 1, framePosition + delta));
    updateTimeline();
  }

  function collectLabel() {
    const eventType = selectedValue("event_type");
    if (!eventType) throw new Error("Choose parking, unparking, or not an event.");
    const notEvent = eventType === "not_event";
    return {
      item_id: payload.item_id,
      event_type: eventType,
      method: notEvent ? "not_applicable" : selectedValue("method"),
      start_index: notEvent ? null : labelStart,
      end_index: notEvent ? null : labelEnd,
      censoring: notEvent ? "not_applicable" : selectedValue("censoring"),
      exclusion_reason: $("exclusionReason").value,
      confidence: selectedValue("confidence"),
      note: $("note").value,
    };
  }

  async function saveAndNext(event) {
    event.preventDefault();
    try {
      setMessage("Saving…");
      const label = collectLabel();
      const saved = await fetchJson(api("/api/label"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(label),
      });
      labels[saved.item_id] = saved;
      updateProgress();
      setMessage(`Saved revision ${saved.revision}`, "success");
      if (itemPosition < reviewIndex.items.length - 1) await loadItem(itemPosition + 1);
      else {
        const firstOpen = reviewIndex.items.findIndex((item) => !labels[item.item_id]);
        if (firstOpen >= 0) await loadItem(firstOpen);
        else setMessage("All 50 labels are saved. Export is ready.", "success");
      }
    } catch (error) {
      setMessage(error.message, "error");
    }
  }

  function bindEvents() {
    $("playButton").addEventListener("click", togglePlayback);
    $("stepBack").addEventListener("click", () => step(-1));
    $("stepForward").addEventListener("click", () => step(1));
    $("timeline").addEventListener("input", (event) => {
      stopPlayback();
      framePosition = Number(event.target.value);
      updateTimeline();
    });
    $("setStart").addEventListener("click", () => {
      labelStart = currentPoint().frame_index;
      updateBoundaryDisplay();
    });
    $("setEnd").addEventListener("click", () => {
      labelEnd = currentPoint().frame_index;
      updateBoundaryDisplay();
    });
    $("clearBoundaries").addEventListener("click", () => {
      labelStart = null;
      labelEnd = null;
      updateBoundaryDisplay();
    });
    document.querySelectorAll('input[name="event_type"]').forEach((input) => input.addEventListener("change", eventTypeChanged));
    document.querySelectorAll('input[name="censoring"]').forEach((input) => input.addEventListener("change", censoringChanged));
    $("labelForm").addEventListener("submit", saveAndNext);
    $("previousItem").addEventListener("click", () => loadItem(itemPosition - 1));
    window.addEventListener("resize", resizeCanvas);
    window.addEventListener("keydown", (event) => {
      if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
      if (event.code === "Space") { event.preventDefault(); togglePlayback(); }
      if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
      if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
      if (event.key === "[") { labelStart = currentPoint().frame_index; updateBoundaryDisplay(); }
      if (event.key === "]") { labelEnd = currentPoint().frame_index; updateBoundaryDisplay(); }
    });
  }

  async function initialize() {
    try {
      bindEvents();
      $("exportLink").href = api("/api/export");
      [reviewIndex, labels] = await Promise.all([
        fetchJson(api("/api/index")),
        fetchJson(api("/api/state")),
      ]);
      updateProgress();
      const firstOpen = reviewIndex.items.findIndex((item) => !labels[item.item_id]);
      await loadItem(firstOpen >= 0 ? firstOpen : 0);
      resizeCanvas();
    } catch (error) {
      setMessage(error.message, "error");
      $("itemTitle").textContent = "Unable to load validation package";
    }
  }

  initialize();
})();
