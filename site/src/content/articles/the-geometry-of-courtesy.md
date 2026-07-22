---
title: "The Geometry of Courtesy"
excerpt: "Why the angle at which we enter a parking space says more about how we treat our neighbors than most of us realize — and what the data on forward and reverse maneuvers actually shows."
tag: "Research"
readTime: "7 min"
publishDate: 2026-07-20
---

Parking lots are among the least examined public spaces we share. We build enormous
infrastructure around them, argue about them in driveways and group chats, and yet rarely
stop to ask a simple question: what does the *shape* of a parking maneuver ask of the people
around us?

## The maneuver as a shared event

A parking maneuver is not a private act. It happens in a lane shared with other drivers,
alongside pedestrians walking to their cars, children trailing behind shopping carts, and
neighbors trying to load groceries. The few seconds it takes to enter or exit a space are
seconds spent inside someone else's field of view — and, sometimes, their path.

Forward parking asks for precision on entry: judging the width of the stall, the swing of the
wheel, the distance to the car alongside you. Reverse parking moves that precision to the
exit, when visibility is often more constrained and the vehicle is sharing the aisle with
active pedestrian and vehicle traffic.

Neither approach eliminates risk. But the *timing* of when precision is required — and who is
around when it happens — changes the texture of the maneuver.

## What the simulation shows

To move past intuition, we built a Monte Carlo kinematic simulation: 10,000 randomized trials
comparing forward (nose-in) and reverse (back-in) parking maneuvers across varying pedestrian
density, aisle width, and adjacent-vehicle visibility. Each trial models Ackermann steering
geometry, driver reaction latency, and line-of-sight obstruction from neighboring vehicles.

Across the full parameter sweep, the simulation logged measurably higher rates of critical
conflicts — moments requiring hard braking to avoid a pedestrian — during the reverse-exit
phase than during the forward-exit phase, concentrated in narrower aisles and higher SUV
presence. The gap narrows as aisle width increases and pedestrian density drops, which matches
the everyday experience that context matters more than dogma.

Full methodology, equations, and results are available in our [research
whitepaper](/research/).

## A case, not a verdict

None of this is an argument that reverse parking is wrong, or that everyone who backs in is
being careless. It is an argument that the *geometry* of a maneuver interacts with the people
around it in a specific, modelable way — and that a small, low-cost habit (facing forward when
practical) shifts that interaction slightly in favor of the people sharing the lot with you.

Courtesy, in this frame, is not a feeling. It's a shape.
