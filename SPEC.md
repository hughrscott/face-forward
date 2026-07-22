# Project Parkway — Scientific Specification & Simulation Design

**Status:** Active  
**Parent Workstream:** [[05_Face Forward]]  
**Execution Plan:** [[05_Face Forward Plan]]  
**Target:** *Annals of Improbable Research* (Ig Nobel Track) / arXiv / Transportation Journals (Two-Stage Rocket)

---

## 1. Architectural Philosophy
The core humor of Project Parkway relies on **disproportionate rigor** — pouring graduate-level kinematics, stochastic modeling, and peer-reviewable LaTeX typesetting into a seemingly mundane debate on parking etiquette. The science must be 100% real; the humor lives in the straight-faced execution and meta-context.

---

## 2. The Physics & Behavioral Model (Python Engine)

### A. Vehicle Kinematics (Ackermann Steering)
Vehicles are modeled in 2D space using a non-holonomic kinematic state model.
* **State Vector:** $S = [x, y, \theta, v]^T$
  * $(x, y)$: Centroid of the rear axle
  * $\theta$: Vehicle heading angle relative to the grid
  * $v$: Velocity along the heading vector
* **Control Input:** $U = [a, \delta]^T$
  * $a$: Acceleration ($m/s^2$)
  * $\delta$: Steering angle of front wheels ($-\delta_{max} \le \delta \le \delta_{max}$)
* **Kinematic Equations of Motion:**
  $$\dot{x} = v \cos\theta$$
  $$\dot{y} = v \sin\theta$$
  $$\dot{\theta} = \frac{v}{L} \tan\delta$$
  $$\dot{v} = a$$
  *(Where $L$ is the vehicle wheelbase, default = 2.8 meters)*

### B. Cognitive & Mechanical Latencies
* **Gear Shift Delay ($\tau_{gear}$):** 1.0 second constant (transition of velocity to zero, lock-to-lock steering, shift $D \leftrightarrow R$).
* **Driver Reaction Latency ($\tau_{react}$):** Stochastic delay drawn from a normal distribution $\mathcal{N}(\mu=0.75s, \sigma=0.15s)$.
* **Visual Obstruction Scan Rate ($\tau_{scan}$):** 0.5s per sweep (left-to-right driver head rotation during reverse exit).

### C. Sensory Field of View (FOV) & Blind Spots
* Reversing blind spots are modeled as geometric shadows cast by adjacent parked vehicles (SUV dimensions: width = 2.0m, length = 5.2m, height = 1.9m).
* The driver's eye position is located $1.2m$ forward of the rear axle.
* The line-of-sight (LOS) vector is blocked if it intersects any adjacent vehicle polygon.

---

## 3. The Monte Carlo Simulation Protocol
The Python simulation (`park_sim.py`) will run $10,000$ iterations under randomized parameters:
* **Pedestrian Density ($\lambda$):** Poisson process generating pedestrians crossing the aisle at a rate of $\lambda \in [0.05, 0.3]\text{ peds/meter}$.
* **Pedestrian Speed:** Stochastic distribution $\mathcal{N}(1.4\text{ m/s}, 0.2\text{ m/s})$.
* **Aisle Width ($A$):** Continuous range from $5.5m$ (tight) to $7.2m$ (generous).
* **Adjacent Vehicle Mix:** Probability of SUV presence in neighboring stalls ($P_{SUV} \in [0.0, 0.8]$).

### Metrics Logged:
1. **Total Cycle Time ($T_{total} = T_{entry} + T_{exit}$)**
2. **Pedestrian Proximity Warnings:** Distance $< 1.5$ meters.
3. **Critical Conflicts (Near-Misses):** Required braking deceleration $a_{brake} > 3.0\text{ m/s}^2$ to avoid collision.
4. **Collision Occurrences:** Intersection of vehicle and pedestrian bounding boxes.

---

## 4. Frontend Web Visualizer (JS Canvas)
To mitigate rendering lag on mobile devices, the visualizer uses a **Hybrid Rendering Strategy**:
* The Python backend generates 100 canonical coordinate pathways representing distinct slider state profiles.
* The JS frontend loads these static JSON paths for the background vehicles, while calculating active driver/pedestrian interactions on the main thread.
* **Collision Broad Phase:** Bounding circles around vehicle nose, center, and rear.
* **Collision Narrow Phase:** separating axis theorem (SAT) for OBB (Oriented Bounding Boxes) only if broad-phase circles overlap.

---

## 5. Journal Submission Strategy (The Two-Stage Rocket)

### Stage 1: Sterile Submission
* Strip all "Face Forward" branding.
* Title: *Kinematic and Cognitive Constraints on Shared-Space Parking Maneuvers: A Comparative Kinematics and Safety Simulation Study.*
* Submit to: *Accident Analysis & Prevention* or *Transportation Research Part F*.
* *Goal:* Earn a peer-reviewed publication based purely on the technical validity of the Ackermann simulation and cognitive latency models.

### Stage 2: The "Improbable" Submission
* Keep the exact same mathematics, but add the "Face Forward" branding, eccentric affiliations, and highly polished visual layout.
* Submit to: *Annals of Improbable Research* (The Ig Nobel pathway).
* *Goal:* Generate massive mainstream media interest through the comical juxtaposition of advanced physics and parking etiquette.

---

## 6. Project Parkway Kanban Pipeline

### Phase 1: Engine & Math
* **T1.1 (pm):** Kinematic and Behavioral Model Specification (LaTeX draft)
* **T1.2 (backend):** Build the Python Kinematic Simulator (`park_sim.py`)
* **T1.3 (backend):** Run 10k Monte Carlo Simulation, generate `simulation_results.csv`
* **T1.4 (backend):** Write Academic Whitepaper draft (`methodology.tex`)

### Phase 2: Web Interface
* **T2.1 (frontend):** Port Kinematics & coordinate paths to `vehicle.js` (HTML5 Canvas/p5.js)
* **T2.2 (frontend):** Build Tailwind UI with sliders (Aisle Width, Pedestrian Density, SUV Presence)
* **T2.3 (pm):** [TEST GATE] Integrate & verify physics coordinate consistency ($\pm5\%$)

### Phase 3: Editorial Launch & Submission
* **T3.1 (gtm):** Write "The Geometry of Courtesy" editorial article
* **T3.2 (gtm):** Draft cover letters and prepare submission packages (arXiv, Elsevier, Improbable Research)
* **T3.3 (frontend):** Stitch landing page, interactive canvas simulator, and whitepaper PDF together.

---

## Quick Links
- Canonical Manifesto: [[Face Forward]]
- Launch Dashboard: [[05_Face Forward]]
- Execution Plan: [[05_Face Forward Plan]]