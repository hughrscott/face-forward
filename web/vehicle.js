/*
 * Project Parkway visualizer — consumes PARK_SIM_CONTRACT.md schema v1.0.
 *
 * Expected canonical_paths.json input (loaded from ../canonical_paths.json):
 * {
 *   "schema_version": "1.0",
 *   "coordinate_frame": "rear_axle_centroid",
 *   "units": { "position": "m", "heading": "rad", "velocity": "m/s" },
 *   "paths": [{
 *     "id": "forward-aisle-6.35",
 *     "strategy": "forward" | "reverse",
 *     "parameters": { "aisle_width_m": 6.35, "stall_width_m": 2.7 },
 *     "points": [{ "x": 0, "y": 0, "theta": 0, "v": 1 }]
 *   }]
 * }
 * Points are ordered rear-axle samples in SI units. `theta` is radians CCW
 * from +x and a negative `v` denotes reverse motion.
 *
 * Dashboard API:
 *   updateParameters(density, aisleWidth, suvPresence)
 * where density is pedestrians/meter (0.05–0.30), aisleWidth is meters
 * (5.50–7.20), and suvPresence is a probability (0.0–0.8).
 */
(() => {
  'use strict';
  p5.disableFriendlyErrors = true;

  const PALETTE = {
    ink: '#101718', asphalt: '#252d2e', asphalt2: '#202829', paper: '#f2efe6',
    amber: '#f1b434', signal: '#e04b3f', mint: '#8eb8a5', line: '#667071', suv: '#596365'
  };
  const BASE_CYCLE = { forward: 16.22, reverse: 18.44 };
  // Proximity thresholds expressed as a fraction of the per-scene pixel scale
  // (scale is ~pixels-per-meter). ~1.0m => collision, ~2.4m => near-miss.
  const COLLISION_RADIUS_M = 1.0;
  const NEAR_MISS_RADIUS_M = 2.4;
  const state = {
    paths: [], aisle: 6.35, density: 0.18, suv: 0.40, paused: false,
    startedAt: performance.now(), pausedAt: 0, pauseOffset: 0,
    counters: { forward: { near: 0, collision: 0 }, reverse: { near: 0, collision: 0 } },
    // Independent per-lane stopwatch state, driven by the start/stop/reset buttons.
    timers: {
      forward: { running: true, elapsedMs: 0, startTs: performance.now() },
      reverse: { running: false, elapsedMs: 0, startTs: 0 }
    },
    // Debounce flags so a pedestrian near the vehicle registers one event per
    // approach rather than once per frame while inside the threshold radius.
    proximity: { forward: {}, reverse: {} },
    // Aggregate counters shown in the bottom "Recorded events" cards; these
    // combine real proximity detections from both lanes with manual +/- adjustments.
    aggregate: { collisions: 0, nearmisses: 0 },
    domTick: 0
  };

  const $ = (id) => document.getElementById(id);
  const clamp = (n, a, b) => Math.max(a, Math.min(b, n));
  const mix = (a, b, t) => a + (b - a) * t;
  const fract = (n) => n - Math.floor(n);
  const hash = (n) => fract(Math.sin(n * 127.1 + 311.7) * 43758.5453);

  function updateParameters(density, aisleWidth, suvPresence) {
    const values = [density, aisleWidth, suvPresence].map(Number);
    if (!values.every(Number.isFinite)) {
      throw new TypeError('updateParameters expects three finite numbers');
    }

    state.density = clamp(values[0], 0.05, 0.30);
    state.aisle = clamp(values[1], 5.50, 7.20);
    state.suv = clamp(values[2], 0, 0.80);

    const densityInput = $('slider-density');
    const aisleInput = $('slider-aisle');
    const suvInput = $('slider-suv');
    if (densityInput) densityInput.value = state.density.toFixed(2);
    if (aisleInput) aisleInput.value = state.aisle.toFixed(2);
    if (suvInput) suvInput.value = String(Math.round(state.suv * 100));
    if ($('ped-value')) $('ped-value').textContent = `${state.density.toFixed(2)} ped/m`;
    if ($('aisle-value')) $('aisle-value').textContent = `${state.aisle.toFixed(2)} m`;
    if ($('suv-value')) $('suv-value').textContent = `${Math.round(state.suv * 100)}%`;

    return { density: state.density, aisleWidth: state.aisle, suvPresence: state.suv };
  }

  window.updateParameters = updateParameters;

  function bindControls() {
    const densityInput = $('slider-density');
    const aisleInput = $('slider-aisle');
    const suvInput = $('slider-suv');
    const speedInput = $('slider-speed');
    const applyControls = () => updateParameters(
      densityInput.value,
      aisleInput.value,
      Number(suvInput.value) / 100
    );
    [densityInput, aisleInput, suvInput].forEach((input) => {
      if (input) input.addEventListener('input', applyControls, { passive: true });
    });
    applyControls();

    // Maneuver speed is not part of the updateParameters contract (that API
    // governs density/aisle/suv only); it directly scales playback rate.
    if (speedInput) {
      const applySpeed = () => {
        state.speed = clamp(Number(speedInput.value) || 1, 0.5, 1.5);
        if ($('speed-value')) $('speed-value').textContent = `${state.speed.toFixed(2)}×`;
      };
      speedInput.addEventListener('input', applySpeed, { passive: true });
      applySpeed();
    }

    $('pause-btn').addEventListener('click', () => {
      state.paused = !state.paused;
      if (state.paused) state.pausedAt = performance.now();
      else state.pauseOffset += performance.now() - state.pausedAt;
      $('pause-btn').textContent = state.paused ? 'Resume run' : 'Pause run';
      $('pause-btn').setAttribute('aria-pressed', String(state.paused));
    });

    bindStopwatch('forward');
    bindStopwatch('reverse');
    bindCounter('collisions', 'counter-collisions-value');
    bindCounter('nearmisses', 'counter-nearmisses-value');
  }

  // --- Stopwatches -------------------------------------------------------
  // Each lane (forward/reverse) has its own independent start/stop/reset
  // stopwatch, wholly separate from the background cycle-progress clock
  // (effectiveElapsed) that drives the canvas animation.
  function startTimer(strategy) {
    const t = state.timers[strategy];
    if (t.running) return;
    t.startTs = performance.now();
    t.running = true;
  }
  function stopTimer(strategy) {
    const t = state.timers[strategy];
    if (!t.running) return;
    t.elapsedMs += performance.now() - t.startTs;
    t.running = false;
  }
  function resetTimer(strategy, { restart = false } = {}) {
    const t = state.timers[strategy];
    t.elapsedMs = 0;
    t.startTs = performance.now();
    t.running = restart;
    // A reset also clears that lane's proximity debounce state and tallies,
    // matching a fresh simulation run for that protocol.
    state.proximity[strategy] = {};
    state.counters[strategy] = { near: 0, collision: 0 };
  }
  function currentTimerMs(strategy) {
    const t = state.timers[strategy];
    return t.running ? t.elapsedMs + (performance.now() - t.startTs) : t.elapsedMs;
  }
  function bindStopwatch(strategy) {
    const startBtn = $(`start-${strategy}`);
    const stopBtn = $(`stop-${strategy}`);
    const resetBtn = $(`reset-${strategy}`);
    if (startBtn) startBtn.addEventListener('click', () => startTimer(strategy));
    if (stopBtn) stopBtn.addEventListener('click', () => stopTimer(strategy));
    if (resetBtn) resetBtn.addEventListener('click', () => {
      // Forward reset behaves like a classic stopwatch reset-to-zero-idle;
      // reverse reset restarts immediately per the "restart on simulation
      // reset" requirement for Protocol B.
      resetTimer(strategy, { restart: strategy === 'reverse' });
    });
  }

  // --- Manual aggregate counters ------------------------------------------
  function bindCounter(name, valueId) {
    const incBtn = $(`inc-${name}`);
    const decBtn = $(`dec-${name}`);
    const key = name === 'collisions' ? 'collisions' : 'nearmisses';
    const render = () => { const el = $(valueId); if (el) el.textContent = state.aggregate[key]; };
    if (incBtn) incBtn.addEventListener('click', () => { state.aggregate[key] += 1; render(); });
    if (decBtn) decBtn.addEventListener('click', () => { state.aggregate[key] = Math.max(0, state.aggregate[key] - 1); render(); });
    render();
  }

  function effectiveElapsed(now) {
    const stop = state.paused ? state.pausedAt : now;
    const speed = state.speed || 1;
    return Math.max(0, (stop - state.startedAt - state.pauseOffset) / 1000) * speed;
  }

  function cycleDuration(strategy) {
    const tightness = (6.35 - state.aisle) * 0.72;
    const exposure = state.density * 2.1 + state.suv * (strategy === 'forward' ? 0.75 : 0.42);
    return Math.max(10, BASE_CYCLE[strategy] + tightness + exposure);
  }

  function nearestPath(strategy) {
    let best = null;
    let delta = Infinity;
    for (const path of state.paths) {
      if (path.strategy !== strategy) continue;
      const d = Math.abs(path.parameters.aisle_width_m - state.aisle);
      if (d < delta) { best = path; delta = d; }
    }
    return best;
  }

  function samplePath(path, progress) {
    if (!path || !path.points.length) return { x: 0, y: 0, theta: 0, v: 0, index: 0 };
    const pos = clamp(progress, 0, 0.999999) * (path.points.length - 1);
    const i = Math.floor(pos);
    const t = pos - i;
    const a = path.points[i], b = path.points[Math.min(i + 1, path.points.length - 1)];
    let dTheta = b.theta - a.theta;
    while (dTheta > Math.PI) dTheta -= Math.PI * 2;
    while (dTheta < -Math.PI) dTheta += Math.PI * 2;
    return { x: mix(a.x, b.x, t), y: mix(a.y, b.y, t), theta: a.theta + dTheta * t, v: mix(a.v, b.v, t), index: i };
  }

  // --- Vehicle-pedestrian proximity detection -----------------------------
  // Called once per pedestrian per frame from renderScene with real world-space
  // positions. Tracks each pedestrian's classification (clear/near/collision)
  // per lane so a single approach only counts once (on the transition into a
  // tighter band), rather than incrementing every frame while inside range.
  function detectProximity(strategy, pedId, vehicleWorld, pedWorld) {
    const dist = Math.hypot(vehicleWorld.x - pedWorld.x, vehicleWorld.y - pedWorld.y);
    const prev = state.proximity[strategy][pedId] || 'clear';
    const next = dist <= COLLISION_RADIUS_M ? 'collision' : dist <= NEAR_MISS_RADIUS_M ? 'near' : 'clear';
    if (next !== prev) {
      if (next === 'collision' && prev !== 'collision') {
        state.counters[strategy].collision += 1;
        state.aggregate.collisions += 1;
        renderAggregateCounters();
      } else if (next === 'near' && prev === 'clear') {
        state.counters[strategy].near += 1;
        state.aggregate.nearmisses += 1;
        renderAggregateCounters();
      }
      state.proximity[strategy][pedId] = next;
    }
  }

  function renderAggregateCounters() {
    const collisionsEl = $('counter-collisions-value');
    const nearEl = $('counter-nearmisses-value');
    if (collisionsEl) collisionsEl.textContent = state.aggregate.collisions;
    if (nearEl) nearEl.textContent = state.aggregate.nearmisses;
  }

  function updateReadouts(now) {
    for (const strategy of ['forward', 'reverse']) {
      const timeEl = $(`${strategy}-time`);
      if (timeEl) timeEl.innerHTML = `${(currentTimerMs(strategy) / 1000).toFixed(2)}<span class="ml-1 text-lg text-paper/35">s</span>`;
      const nearEl = $(`${strategy}-near`);
      if (nearEl) nearEl.textContent = state.counters[strategy].near;
      const collisionEl = $(`${strategy}-collision`);
      if (collisionEl) collisionEl.textContent = state.counters[strategy].collision;
      const duration = cycleDuration(strategy);
      const cycleEl = $(`${strategy}-cycle`);
      if (cycleEl) cycleEl.innerHTML = `${duration.toFixed(2)}<span class="text-xs text-paper/35">s</span>`;
    }
  }

  function vehicleShape(p, cx, cy, theta, scale, strategy, velocity) {
    const bodyLength = 4.65 * scale, width = 1.86 * scale, rearOverhang = 0.95 * scale;
    p.push();
    p.translate(cx, cy);
    p.rotate(-theta); // world y is inverted by projection
    p.noStroke();
    p.fill(0, 0, 0, 46); p.rect(-rearOverhang + 3, -width / 2 + 4, bodyLength, width, 5);
    p.fill(strategy === 'forward' ? PALETTE.mint : PALETTE.amber);
    p.rect(-rearOverhang, -width / 2, bodyLength, width, 4);
    p.fill(PALETTE.ink); p.rect(0.08 * scale, -width * .39, 1.72 * scale, width * .78, 2);
    p.fill('#9fb1b0'); p.rect(0.26 * scale, -width * .32, .58 * scale, width * .64, 1);
    p.fill('#536263'); p.rect(1.03 * scale, -width * .32, .55 * scale, width * .64, 1);
    p.stroke(PALETTE.ink); p.strokeWeight(Math.max(1, scale * .04));
    p.line(0, -width / 2, 0, width / 2);
    p.noStroke(); p.fill(PALETTE.signal);
    const lightX = velocity < 0 ? -rearOverhang - 1 : bodyLength - rearOverhang - 3;
    p.rect(lightX, -width * .4, 4, width * .18); p.rect(lightX, width * .22, 4, width * .18);
    // rear-axle centroid — source contract origin
    p.stroke(PALETTE.paper); p.strokeWeight(1); p.line(-4, 0, 4, 0); p.line(0, -4, 0, 4);
    p.pop();
  }

  function renderScene(p, scene, strategy, progress, elapsed) {
    const { x, y, w, h } = scene;
    const path = nearestPath(strategy);
    const sample = samplePath(path, progress);
    const margin = 24;
    const scale = clamp(w / 15.2, 28, 46);
    const originX = x + w * .48;
    const originY = y + h * .80;
    const worldToScreen = (wx, wy) => ({ x: originX + wx * scale, y: originY - wy * scale });

    p.push(); p.noStroke(); p.fill(strategy === 'forward' ? '#273130' : '#302e28'); p.rect(x, y, w, h);
    // asphalt aggregate: deterministic, sparse, and static-looking
    p.stroke(255, 255, 255, 9); p.strokeWeight(1);
    for (let i = 0; i < 90; i += 1) {
      const dx = hash(i + (strategy === 'forward' ? 2 : 8)) * w;
      const dy = hash(i + 200) * h;
      p.point(x + dx, y + dy);
    }
    // aisle and stall geometry
    p.noStroke(); p.fill('#202728'); p.rect(x, originY - 5.0 * scale, w, 5.0 * scale);
    p.stroke(242, 239, 230, 72); p.strokeWeight(1);
    p.line(x, originY, x + w, originY);
    for (let stall = -3; stall <= 3; stall += 1) {
      const sx = originX + stall * 2.7 * scale;
      p.line(sx, originY - 5.0 * scale, sx, originY);
    }
    p.drawingContext.setLineDash([8, 9]); p.stroke(241, 180, 52, 75);
    p.line(x + margin, originY - state.aisle * .46 * scale, x + w - margin, originY - state.aisle * .46 * scale);
    p.drawingContext.setLineDash([]);

    // parked SUVs and their geometric line-of-sight shadows
    const suvCount = Math.round(state.suv * 4);
    for (let i = 0; i < suvCount; i += 1) {
      const stall = i % 2 === 0 ? -2 - Math.floor(i / 2) : 1 + Math.floor(i / 2);
      const carX = originX + (stall + .5) * 2.7 * scale;
      const carY = originY - 2.6 * scale;
      p.noStroke(); p.fill(PALETTE.suv); p.rect(carX - 1.0 * scale, carY - 2.6 * scale, 2.0 * scale, 5.2 * scale, 3);
      p.fill('#354143'); p.rect(carX - .77 * scale, carY - 1.3 * scale, 1.54 * scale, 2.0 * scale, 2);
      const eye = worldToScreen(sample.x + Math.cos(sample.theta) * 1.2, sample.y + Math.sin(sample.theta) * 1.2);
      const edgeX = carX + (stall < 0 ? 1 : -1) * scale;
      p.noStroke(); p.fill(224, 75, 63, 18 + state.suv * 35);
      p.triangle(eye.x, eye.y, edgeX, originY - 5.2 * scale, edgeX + (stall < 0 ? -1 : 1) * 2.8 * scale, y);
      p.stroke(224, 75, 63, 65); p.strokeWeight(.75); p.line(eye.x, eye.y, edgeX, originY - 5.2 * scale);
    }

    // validated trajectory trace, rendered once from static coordinate samples
    if (path) {
      p.noFill(); p.stroke(strategy === 'forward' ? PALETTE.mint : PALETTE.amber); p.strokeWeight(1.4);
      p.beginShape();
      for (const point of path.points) { const s = worldToScreen(point.x, point.y); p.vertex(s.x, s.y); }
      p.endShape();
      p.noFill(); p.stroke(242,239,230,24); p.strokeWeight(4);
      p.beginShape();
      const upto = Math.max(2, sample.index);
      for (let i = 0; i < upto; i += 1) { const s = worldToScreen(path.points[i].x, path.points[i].y); p.vertex(s.x, s.y); }
      p.endShape();
    }

    // pedestrians are light-weight agents crossing the aisle; count derives from density.
    const pedCount = Math.max(1, Math.round(state.density * 18));
    const pedestrianScreenPositions = [];
    for (let i = 0; i < pedCount; i += 1) {
      const phase = fract(elapsed * (0.055 + hash(i + 50) * .018) + hash(i + 77));
      const px = x + 34 + phase * (w - 68);
      const laneY = originY - (2.1 + hash(i + 31) * 2.3) * scale;
      pedestrianScreenPositions.push({ id: i, px, laneY });
      p.noStroke(); p.fill(PALETTE.paper); p.circle(px, laneY, 5);
      p.stroke(242,239,230,115); p.strokeWeight(1); p.line(px, laneY + 3, px, laneY + 12);
      p.line(px, laneY + 7, px - 4, laneY + 11); p.line(px, laneY + 7, px + 4, laneY + 10);
      p.line(px, laneY + 12, px - 3, laneY + 17); p.line(px, laneY + 12, px + 4, laneY + 17);
    }

    const car = worldToScreen(sample.x, sample.y);
    vehicleShape(p, car.x, car.y, sample.theta, scale, strategy, sample.v);

    // Vehicle-pedestrian proximity, evaluated in world-space meters (screen
    // pixel distance divided by the scene's pixels-per-meter scale) so the
    // collision/near-miss thresholds are consistent across canvas sizes.
    for (const ped of pedestrianScreenPositions) {
      const screenDist = Math.hypot(car.x - ped.px, car.y - ped.laneY);
      detectProximity(strategy, ped.id, { x: 0, y: 0 }, { x: screenDist / scale, y: 0 });
    }

    // panel metadata
    p.noStroke(); p.fill(PALETTE.ink); p.rect(x + 13, y + 13, 150, 39);
    p.fill(strategy === 'forward' ? PALETTE.mint : PALETTE.amber); p.textFont('IBM Plex Mono'); p.textSize(10); p.textStyle(p.BOLD);
    p.text(strategy === 'forward' ? 'A / FORWARD' : 'B / REVERSE', x + 23, y + 29);
    p.fill(242,239,230,120); p.textStyle(p.NORMAL); p.textSize(8);
    p.text(path ? `PATH ${path.parameters.aisle_width_m.toFixed(2)} m · ${path.points.length} SAMPLES` : 'PATH UNAVAILABLE', x + 23, y + 43);
    p.fill(242,239,230,70); p.textSize(8); p.textAlign(p.RIGHT);
    p.text(`v ${sample.v.toFixed(2)} m/s   θ ${sample.theta.toFixed(2)} rad`, x + w - 18, y + 28);
    p.textAlign(p.LEFT);
    p.pop();
  }

  const sketch = (p) => {
    let parentWidth = 1200;
    const fit = () => {
      const node = $('sim-canvas');
      parentWidth = Math.max(320, node.clientWidth);
      return { w: Math.round(parentWidth), h: Math.round(clamp(parentWidth * .42, 430, 650)) };
    };
    p.setup = () => {
      const size = fit();
      const canvas = p.createCanvas(size.w, size.h);
      canvas.parent('sim-canvas');
      p.pixelDensity(1); p.frameRate(60); p.textFont('IBM Plex Mono');
    };
    p.draw = () => {
      const now = performance.now();
      const elapsed = effectiveElapsed(now);
      p.background(PALETTE.asphalt);
      const gap = p.width > 700 ? 2 : 2;
      const vertical = p.width < 700;
      if (vertical) {
        const half = (p.height - gap) / 2;
        for (const strategy of ['forward', 'reverse']) {
          const index = strategy === 'forward' ? 0 : 1;
          const duration = cycleDuration(strategy);
          renderScene(p, { x: 0, y: index * (half + gap), w: p.width, h: half }, strategy, (elapsed % duration) / duration, elapsed);
        }
      } else {
        const half = (p.width - gap) / 2;
        for (const strategy of ['forward', 'reverse']) {
          const index = strategy === 'forward' ? 0 : 1;
          const duration = cycleDuration(strategy);
          renderScene(p, { x: index * (half + gap), y: 0, w: half, h: p.height }, strategy, (elapsed % duration) / duration, elapsed);
        }
      }
      if (now - state.domTick > 80) {
        updateReadouts(now);
        $('fps-readout').textContent = `Renderer · ${Math.round(p.frameRate())} fps · ${state.paths.length} paths resident`;
        state.domTick = now;
      }
    };
    p.windowResized = () => {
      const size = fit();
      p.resizeCanvas(size.w, size.h);
    };
  };

  async function boot() {
    bindControls();
    try {
      const response = await fetch('../canonical_paths.json');
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      if (payload.schema_version !== '1.0' || payload.coordinate_frame !== 'rear_axle_centroid' || !Array.isArray(payload.paths)) {
        throw new Error('Unsupported trajectory contract');
      }
      state.paths = payload.paths;
      new p5(sketch);
      window.__parkwayReady = true;
    } catch (error) {
      console.error('Project Parkway boot failure:', error);
      $('load-error').hidden = false;
      $('fps-readout').textContent = 'Renderer · data fault';
      window.__parkwayReady = false;
    }
  }

  boot();
})();
