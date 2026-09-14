# Battery Fundamentals for the EV Battery Digital Twin

## 1. Glossary

| Term                                             | Definition                                                                                                                                                                              | Relevance to this project                                                               |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **Lithium-ion (Li-ion) battery**                 | A rechargeable battery in which lithium ions move between the negative and positive electrodes during charge/discharge.                                                                 | Primary battery technology represented by the datasets.                                 |
| **Cell**                                         | The fundamental electrochemical unit of a battery. A cell has its own voltage, capacity, temperature and degradation behavior.                                                          | NASA/Oxford datasets are primarily cell-level; this is an important project limitation. |
| **Battery pack**                                 | A collection of cells electrically and mechanically integrated to meet voltage, capacity and power requirements.                                                                        | The eventual real-world target of an EV battery system.                                 |
| **Battery Management System (BMS)**              | Hardware/software responsible for monitoring and controlling battery operation, including voltage, current, temperature, SOC and safety limits.                                         | The digital twin can conceptually complement BMS measurements.                          |
| **State of Charge (SOC)**                        | Estimate of the charge currently available relative to the battery's usable capacity. Roughly analogous to a fuel gauge.                                                                | Useful for describing the instantaneous operating state.                                |
| **State of Health (SOH)**                        | A measure of how the battery's current condition compares with a reference healthy condition.                                                                                           | Primary health-estimation target.                                                       |
| **Remaining Useful Life (RUL)**                  | The expected remaining operating life before a predefined end-of-life criterion is reached.                                                                                             | Primary prognostics target.                                                             |
| **Capacity**                                     | Amount of electric charge a battery can deliver under specified test conditions, usually measured in Ah.                                                                                | Main observable used to define capacity-based SOH.                                      |
| **Energy**                                       | Electrical energy delivered or stored, commonly measured in Wh.                                                                                                                         | Useful because capacity alone does not fully represent usable energy.                   |
| **C-rate**                                       | Charge/discharge current normalized by nominal capacity. 1C approximately corresponds to charging/discharging the nominal capacity in one hour under idealized conditions.              | Represents charging/load intensity.                                                     |
| **Cycle**                                        | A defined charging/discharging operation. In real EV use, a "cycle" may be better represented by an equivalent full cycle based on cumulative throughput.                               | Primary time/aging index in the experimental datasets.                                  |
| **Equivalent Full Cycle (EFC)**                  | A normalized measure of cumulative charge throughput corresponding to one full charge/discharge of the nominal capacity.                                                                | Useful for comparing different usage patterns.                                          |
| **Internal resistance**                          | Effective resistance associated with the battery's electrical behavior. It tends to increase as batteries age, although its measurement depends on method and operating conditions.     | Candidate degradation/health feature.                                                   |
| **Impedance**                                    | Frequency-dependent opposition to AC current, containing information about electrochemical processes.                                                                                   | Can provide additional degradation information where available.                         |
| **Electrochemical impedance spectroscopy (EIS)** | Technique for measuring impedance over a range of frequencies.                                                                                                                          | Useful diagnostic signal in some battery datasets.                                      |
| **Degradation**                                  | Gradual loss or alteration of battery performance caused by physical and chemical changes.                                                                                              | Central phenomenon being modeled.                                                       |
| **Calendar aging**                               | Degradation caused by time, even when the battery is not actively cycled.                                                                                                               | Important in real EVs but incompletely represented by cycling-focused datasets.         |
| **Cycle aging**                                  | Degradation caused by repeated charging/discharging.                                                                                                                                    | Dominant aging mode represented in the selected datasets.                               |
| **SEI**                                          | Solid Electrolyte Interphase, a passivation layer formed primarily at the negative electrode. It is necessary for stable operation but can continue to grow and consume active lithium. | One important degradation mechanism.                                                    |
| **Lithium plating**                              | Deposition of metallic lithium on the negative electrode, potentially occurring under unfavorable charging conditions such as low temperature or high charging rates.                   | Important safety and degradation mechanism.                                             |
| **Loss of Lithium Inventory (LLI)**              | Reduction in the amount of cyclable lithium available for normal cell operation.                                                                                                        | One mechanism behind capacity fade.                                                     |
| **Loss of Active Material (LAM)**                | Reduction in electrochemically active electrode material.                                                                                                                               | Another mechanism behind capacity/power degradation.                                    |
| **Thermal runaway**                              | Uncontrolled exothermic reaction sequence that can cause rapid temperature rise and potentially catastrophic battery failure.                                                           | Outside the main scope of the prognostics MVP, but relevant to safety.                  |
| **End of Life (EOL)**                            | Operational threshold at which a battery is considered no longer suitable for its defined application.                                                                                  | Defines the target boundary for RUL.                                                    |
| **Digital twin**                                 | A dynamic digital representation of a physical asset that is updated using observations and can represent current/future states and support simulation.                                 | Overall system concept.                                                                 |
| **CC-CV charging** | Constant-Current charging until a terminal voltage is reached, then Constant-Voltage holding while current decays to a cut-off. | The NASA charging protocol; the CC/CV phase split is a primary source of health features. |
| **Cut-off voltage** | The voltage at which a discharge (or a CV charge current) is terminated. | Differs per NASA cell (~2.2–2.7 V), so raw capacities are not directly comparable across cells. |
| **Capacity regeneration** | Apparent recovery of measured capacity after a rest period, caused by relaxation rather than by reversal of degradation. | Makes the NASA SOH curve non-monotonic; must be handled, not deleted. |
| **Knee point** | The cycle at which capacity fade transitions from gradual to sharply accelerated. | Target of change-point detection; the main reason early-life RUL prediction is hard. |
| **Health Indicator (HI)** | A measurable or derived quantity that correlates with internal battery state and can be observed without a full capacity test. | The bridge between degradation physics and model features. |
| **Incremental Capacity Analysis (ICA)** | Analysis of dQ/dV versus voltage; peaks correspond to electrode phase transitions. | Peak height/position/area track LAM and LLI separately. |
| **Differential Voltage Analysis (DVA)** | Analysis of dV/dQ versus capacity; the complementary view to ICA. | Used to attribute fade to specific electrodes. |
| **Coulombic efficiency (CE)** | Discharge charge divided by charge charge for the same cycle. | Values below 1 indicate charge consumed by side reactions; a sensitive early degradation signal. |

---

# 2. How Lithium-Ion Batteries Degrade

Battery degradation is not a single phenomenon. It is the combined result of several electrochemical, mechanical and thermal processes.

A useful conceptual representation is:

```text
                    Battery usage
                         |
          +--------------+--------------+
          |              |              |
       Current        Temperature     SOC window
          |              |              |
          +--------------+--------------+
                         |
                         v
                Electrochemical stress
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
      SEI growth     Li plating       Electrode
                                     degradation
          |              |              |
          +--------------+--------------+
                         |
                         v
                Internal state changes
                         |
             +-----------+-----------+
             |                       |
             v                       v
        Capacity fade          Resistance rise
             |                       |
             +-----------+-----------+
                         |
                         v
                 Reduced battery life
```

## 2.1 SEI growth and loss of lithium inventory

The Solid Electrolyte Interphase forms on the electrode surface during battery operation. It is essential because it protects the electrode/electrolyte interface, but continued growth consumes electrolyte and cyclable lithium.

The practical consequence is that less lithium remains available to shuttle between electrodes.

This contributes to:

* capacity loss;
* increasing impedance;
* changes in voltage behavior; and
* reduced power capability.

From a data-science perspective, we may not directly observe SEI growth in ordinary voltage/current/temperature telemetry. Instead, we observe its **effects** through variables such as capacity, resistance and voltage curves.

---

## 2.2 Lithium plating

Under unfavorable charging conditions, metallic lithium can deposit on the negative electrode instead of being properly intercalated.

Risk increases under conditions such as:

* low temperature;
* high charging current;
* high SOC;
* certain combinations of aging and charging conditions.

Lithium plating is particularly important because it can contribute both to degradation and safety risk.

However, a critical modeling point is that **ordinary telemetry does not necessarily prove lithium plating occurred**. A model may identify operating conditions associated with plating risk, but that should not be presented as direct chemical diagnosis without suitable measurements.

---

## 2.3 Loss of active material

Electrode materials can gradually become electrochemically inactive because of structural changes, cracking, particle degradation or other mechanisms.

This is commonly described as **Loss of Active Material (LAM)**.

LAM can reduce the amount of material available to store and release lithium, leading to declining capacity.

Again, the project should treat LAM as a physical explanation for observed degradation rather than claiming that the ML model directly measures LAM.

---

## 2.4 Resistance growth

As a battery ages, its effective internal resistance/impedance can increase.

This matters because higher resistance can cause:

* larger voltage drops under load;
* increased heat generation;
* reduced power capability;
* lower usable energy under demanding conditions.

Therefore, resistance is a useful complementary health signal.

A battery could theoretically retain considerable capacity while its power capability has already deteriorated substantially. This is one reason why **SOH should not be thought of as a single universal number**.

---

## 2.5 Thermal degradation

Temperature affects battery degradation strongly.

High temperatures can accelerate undesirable chemical reactions, while very low temperatures can increase resistance and make some operating conditions more challenging.

This creates an important feedback loop:

```text
Higher load
    ↓
Higher heat generation
    ↓
Higher temperature
    ↓
Faster degradation
    ↓
Higher resistance
    ↓
More heat under load
```

This is one reason temperature should be treated as a first-class variable in the digital twin.

---

## 2.6 The shape of the degradation curve

Degradation is not linear in cycle count. A typical Li-ion capacity trajectory has three regimes:

* **Regime 1 — early life, sublinear.**
  * Dominated by SEI formation and growth.
  * SEI growth is diffusion-limited, so fade follows roughly a **square-root-of-time** law: fast at first, then slowing.
  * A cell can lose 2–3% capacity in its first tens of cycles and then appear to stabilise.

* **Regime 2 — mid life, approximately linear.**
  * SEI growth continues slowly; LAM accumulates gradually.
  * This is the regime where simple linear extrapolation of the fade rate works reasonably well.

* **Regime 3 — the knee, superlinear.**
  * Fade rate increases sharply and often irreversibly.
  * Commonly attributed to: onset of lithium plating, electrolyte depletion/dry-out, pore clogging by SEI, or loss of electrode percolation as LAM passes a threshold.
  * Once past the knee, remaining life is short.

**Why this matters for the project:**

* An EOL threshold is usually crossed **during or after** the knee, so RUL is dominated by *when the knee occurs*, not by the mid-life fade rate.
* A linear-extrapolation RUL baseline will **systematically over-predict** remaining life for any cell that has not yet kneed. This is an expected, reportable failure mode — use it as the baseline to beat.
* Early-life prediction is hard precisely because the knee has not yet appeared in the observed history. Any model claiming accurate early RUL is implicitly claiming it can infer knee onset from pre-knee signals.
* Knee onset is the natural target for change-point detection and for the anomaly layer.

---

## 2.7 Capacity regeneration (self-recovery)

The single most important dataset-specific quirk in the NASA data.

**What is observed:**

* Measured discharge capacity at cycle *t* is sometimes **higher** than at cycle *t−1*.
* The SOH trajectory is therefore **non-monotonic**: a general downward trend punctuated by upward jumps.
* Jumps are commonly a few percent of SOH and appear after longer idle periods between cycles.

**Why it happens:**

* It is a **relaxation effect, not a reversal of degradation.** No lost lithium is recovered.
* During rest, lithium concentration gradients inside the electrode particles equalise, and local SOC inhomogeneity relaxes.
* Some interfacial and diffusion-limited effects partially recover, so the next discharge can extract more charge before hitting the cut-off voltage.
* The NASA experiments were run in batches with real idle time between sessions, which is why the effect is so visible.

**Why it matters:**

* **Do not remove these points as outliers.** They are genuine measurements and a documented property of the dataset. Deleting them is a known way to produce an artificially clean, unrealistic result.
* Any model or feature that assumes **monotonic** SOH will be violated by the data.
* It breaks a naive EOL rule. If EOL is "first cycle where SOH ≤ 70%", a cell can cross the threshold, regenerate back above it, and cross again. **The crossing rule must be defined explicitly** — for example, the first cycle after which SOH stays at or below the threshold for *k* consecutive cycles.
* It is also an opportunity: **rest duration before a cycle** becomes a legitimate, causally-available feature, and the regeneration magnitude is itself a health signal.

**How to handle it:**

* Keep the raw signal as the source of truth.
* Optionally derive a smoothed or monotone-envelope version for trend/slope estimation — but the smoothing must be **backward-looking only**, never centred, or it leaks future information.
* Record the decision and the EOL crossing rule in the evaluation protocol.

---

## 2.8 The CC-CV charging protocol and why it encodes health

Capacity is only measurable through a controlled full discharge, which is rarely available in a real vehicle. The **charging** curve, by contrast, is always observed — which is why charge-phase features are the most deployable health indicators.

**The protocol:**

* **CC phase** — charge at a fixed current until the terminal voltage reaches an upper limit.
  * NASA: approximately 1.5 A constant current up to 4.2 V *(verify against the files in Phase 2)*.
* **CV phase** — hold the voltage at that limit while the current decays, until it falls below a small cut-off.
  * NASA: hold at 4.2 V until current drops to roughly 20 mA.
* **Discharge** — constant current (NASA: about 2 A) down to a per-cell cut-off voltage, typically in the 2.2–2.7 V range.

**Why CC duration shortens as a cell ages:**

* The aged cell holds less charge, so it fills sooner.
* Internal resistance has risen, so the IR drop is larger; the *measured terminal* voltage reaches 4.2 V while the true electrode potential is still lower.
* Both effects push the CC→CV transition earlier, so **less charge is delivered during the CC phase**.

**The useful corollaries:**

* The **CV phase lengthens** to compensate — the cell finishes charging at declining current instead.
* The **CC/CV duration ratio** therefore falls monotonically with age, and being a ratio it is more robust than either absolute duration.
* **Time to charge between two fixed voltages** (e.g. 3.8 V → 4.2 V) is a compact, well-established HI.
* These are computed from charge data alone — **no full discharge test required** — which is what makes them realistic for a deployed twin.

**A practical trap:**

* NASA cells use **different discharge cut-off voltages**. A lower cut-off extracts more Ah from the same cell.
* So raw capacity is **not directly comparable across cells** without accounting for cut-off voltage. This is one reason SOH is normalised per cell rather than compared in absolute Ah.

---

## 2.9 From mechanism to observable: the feature bridge

The mechanisms in §2.1–2.5 are not directly measurable from ordinary telemetry. What follows is how each one *shows up* in the data — this mapping is the justification for every engineered feature.

| Mechanism | What it does internally | What you can actually observe |
|---|---|---|
| **SEI growth / LLI** | Consumes cyclable lithium | Capacity fade; CC-charge duration shortens; dQ/dV peaks **shift** in voltage (electrode slippage); coulombic efficiency below 1 |
| **LAM** | Less active electrode material | Capacity fade; dQ/dV peaks **shrink, broaden or merge**; voltage curve changes shape, not just position |
| **Impedance rise** | Higher internal resistance | Larger voltage sag at load onset; discharge reaches cut-off sooner at the same current; more heat per cycle; rising Re and Rct |
| **Lithium plating** | Metallic Li deposits | Sudden acceleration of fade (a knee); sometimes a distinctive voltage plateau during rest after charge |
| **Thermal stress** | Accelerates side reactions | Higher mean and peak cycle temperature; a faster observed fade rate for the same throughput |

**Incremental Capacity Analysis (dQ/dV) — the highest-value derived signal:**

* Plot dQ/dV against voltage instead of capacity against cycle.
* **Peaks** correspond to electrode phase transitions during lithium insertion/extraction.
* **Peak height and area** scale with the amount of active material → track **LAM**.
* **Peak position shift** tracks the relative alignment of the two electrodes → tracks **LLI**.
* This means ICA can partially **separate degradation modes**, which raw capacity alone cannot.
* Practical cautions: it is a numerical derivative of noisy data, so it needs smoothing and a low C-rate to be meaningful; validate the peaks look stable before trusting features built on them.

**ΔQ(V) — the Severson feature family:**

* Instead of one cycle's curve, take the **difference in discharge capacity between two cycles, as a function of voltage** — e.g. cycle 100 minus cycle 10.
* Summary statistics of that difference curve (variance, minimum, skewness) are strong predictors of eventual cycle life, **even while capacity fade is still negligible**.
* This is the reference approach for genuinely early prediction, and worth reproducing in Phase 6.

**Resulting feature families for Phase 6:**

* Charge-phase timing — CC duration, CV duration, CC/CV ratio, time between fixed voltages.
* Discharge-phase shape — duration, plateau length, mean/min voltage, delivered energy.
* Curve-derivative — dQ/dV peak height, position, area; ΔQ(V) statistics.
* Thermal — mean, max and delta temperature; time above threshold; time to peak.
* Impedance — Re, Rct and their backward-looking deltas.
* Efficiency and usage — coulombic efficiency, coulomb throughput, rest duration before cycle.

---

# 3. Why Define SOH Using Capacity?

There is no single universally correct definition of SOH.

For this project, the most practical starting point is **capacity-based SOH**:

$$
SOH_t = \frac{Q_t}{Q_{ref}}\times100
$$

where:

* \(Q_t\) = measured usable capacity at time/cycle \(t\)
* \(Q_{ref}\) = reference capacity of the healthy battery.

For example:

```text
Initial capacity = 2.00 Ah
Current capacity = 1.70 Ah

SOH = 1.70 / 2.00 × 100
    = 85%
```

## Why capacity?

Capacity is a useful primary definition because it directly represents how much charge the battery can deliver under specified conditions.

It is also:

1. **Physically meaningful** — capacity is directly related to usable energy storage.
2. **Measurable** — capacity can be obtained from controlled charge/discharge experiments.
3. **Comparable** — degradation can be represented as a normalized percentage.
4. **Suitable for aging trajectories** — repeated measurements produce a clear health curve.
5. **Compatible with the selected datasets** — the aging experiments provide capacity measurements that allow health trajectories to be constructed.

The NASA dataset's experimental EOL definition is particularly convenient for this project because it uses a capacity-fade threshold: the battery reaches EOL when capacity falls from 2 Ah to 1.4 Ah.

Two caveats to record now:

* **NASA's 1.4 Ah threshold is 70% SOH, not the 80% used as the common industry convention** for automotive second-life/replacement decisions. RUL values from this project are therefore *not* directly comparable to published figures that assume an 80% EOL. State the threshold wherever an RUL number is reported.
* **Not every cell necessarily reaches the threshold** within its recorded cycles. Cells that never cross EOL are **right-censored**: their true RUL is unknown, only bounded. They must be excluded from RUL training or handled with an explicit, documented censoring method — never assigned an invented EOL cycle.

---

## 3.1 Why SOH is not simply "battery condition"

Capacity-based SOH has limitations.

Consider two batteries:

```text
Battery A
Capacity: 90%
Resistance: Low

Battery B
Capacity: 90%
Resistance: High
```

Both could have the same capacity-based SOH while having different power capabilities and operating behavior.

Therefore, our digital twin should maintain a **multi-dimensional health state**:

```text
              Battery Health
                    |
       +------------+------------+
       |            |            |
     Capacity    Resistance   Thermal
       |            |            |
      SOH       Power health   Risk
```

Capacity-based SOH remains the **primary benchmark target**, while other variables enrich the digital representation.

---

# 4. Why Define RUL the Way We Do?

RUL answers a different question.

SOH asks:

> **"How healthy is the battery now?"**

RUL asks:

> **"How long can the battery continue operating before it reaches a predefined limit?"**

For a cycle-based experiment:

$$
RUL_t = N_{EOL} - N_t
$$

where:

* \(N_t\) = current cycle;
* \(N_{EOL}\) = cycle where the EOL criterion is reached.

Example:

```text
Cycle 420
SOH = 82%

Predicted EOL = cycle 610

RUL = 610 - 420
    = 190 cycles
```

This definition is useful because it converts a degradation trajectory into an actionable prognostic quantity.

---

## 4.1 Why EOL must be explicitly defined

"Battery is dead" is not a sufficiently precise target.

A battery can still function after substantial degradation. Whether it is considered useful depends on the application.

For example, one application may define EOL around:

* a capacity threshold;
* a power-capability threshold;
* an impedance threshold;
* a safety threshold; or
* a combination of constraints.

Therefore:

> **RUL is always RUL relative to a specified failure/end-of-life criterion.**

For this project, capacity-based EOL is chosen because it is directly supported by the primary aging dataset.

---

# 5. Why Predict RUL From Partial History?

A trivial model could look at an entire battery trajectory, identify its EOL cycle, and report the remaining cycles.

That is not useful in practice because the future trajectory is unavailable at prediction time.

The actual problem is:

```text
Observed history
     |
     | cycles 1 → 300
     v
+----------------+
| RUL prediction |
+----------------+
     |
     v
Predicted future
     |
     v
EOL estimate
```

At cycle 300, the model should **not know what happened at cycles 301–600**.

This makes temporal leakage control one of the most important aspects of the project.

---

# 6. Limitations of Cell-Level Data

This is probably the most important caveat in the entire project.

The primary datasets are predominantly **cell-level experimental datasets**.

A real EV battery is not a single cell.

A simplified EV battery hierarchy is:

```text
Battery Pack
    |
    +--- Module
    |      |
    |      +--- Cell
    |      +--- Cell
    |      +--- Cell
    |
    +--- Module
           |
           +--- Cell
           +--- Cell
           +--- Cell
```

Therefore:

> **A model trained on individual cells is not automatically a model of an EV battery pack.**

---

## 6.1 Cell-to-cell variability

Real cells are not identical.

They can differ in:

* manufacturing variation;
* capacity;
* internal resistance;
* temperature;
* aging rate;
* operating history.

Suppose eight cells are connected in a simplified series group:

```text
Cell 1   SOH = 94%
Cell 2   SOH = 92%
Cell 3   SOH = 91%
Cell 4   SOH = 89%
Cell 5   SOH = 88%
Cell 6   SOH = 87%
Cell 7   SOH = 84%
Cell 8   SOH = 73%  ← weak cell
```

The pack's behavior cannot be represented simply by averaging the eight SOH values.

The weakest cell may constrain the usable operating range.

---

## 6.2 Thermal interactions

An isolated laboratory cell does not fully represent pack thermal behavior.

A real pack contains:

* neighboring heat sources;
* cooling systems;
* thermal gradients;
* module-level heat transfer;
* mechanical constraints.

Therefore:

```text
Cell temperature
        ≠
Pack thermal state
```

A production battery twin would need thermal information at a much richer spatial scale.

---

## 6.3 Electrical configuration matters

Cells can be connected:

* in series;
* in parallel;
* in series-parallel configurations.

The electrical topology affects:

* pack voltage;
* pack capacity;
* current distribution;
* cell balancing;
* weak-cell behavior;
* fault propagation.

A cell-level model does not automatically capture these effects.

---

## 6.4 BMS behavior is missing or simplified

A real EV BMS actively influences operation.

It may perform:

* SOC estimation;
* balancing;
* thermal management;
* charging control;
* current limiting;
* safety protection;
* fault detection.

Experimental datasets generally do not reproduce the complete closed-loop interaction between a production BMS and a vehicle.

Therefore, the project should not claim to be a replacement for a BMS.

---

## 6.5 Limited operating diversity

Laboratory aging experiments are controlled and often cover a narrower range of conditions than real vehicles.

Real EVs experience:

* traffic;
* regenerative braking;
* fast charging;
* ambient temperature changes;
* different driving styles;
* long periods of parking;
* varying terrain;
* seasonal conditions.

Therefore, excellent performance on a laboratory dataset does not necessarily mean excellent performance in a vehicle.

---

# 7. How We Should Handle These Limitations

Rather than hiding the limitations, make them part of the research design.

## Level 1 — Cell digital twin

Start with:

```text
Experimental cell telemetry
        ↓
Cell state estimation
        ↓
Cell degradation model
        ↓
Cell RUL prediction
```

This is the scientifically defensible MVP.

## Level 2 — Virtual pack

Construct a simulated pack from multiple virtual cells:

```text
              Virtual Pack
                   |
        +----------+----------+
        |          |          |
     Cell A     Cell B     Cell C
      SOH 93%    SOH 89%    SOH 74%
        |          |          |
        +----------+----------+
                   ↓
             Pack behavior
```

Introduce:

* cell-to-cell variability;
* resistance variation;
* thermal differences;
* weakest-cell effects;
* balancing assumptions.

## Level 3 — Pack digital twin

Eventually add:

```text
Pack telemetry
     ↓
Cell state estimation
     ↓
Cell twins
     ↓
Pack aggregation
     ↓
Pack-level health
     ↓
Pack-level RUL
```

This creates a much stronger bridge between the public datasets and the EV application.

---

# 8. Final Conceptual Model

The project should ultimately distinguish three things:

```text
                 OBSERVATIONS
                     |
             Voltage / Current
             Temperature / Time
             Capacity / Impedance
                     |
                     v
             ┌───────────────┐
             │  State Model  │
             └───────┬───────┘
                     |
                     v
              DIGITAL TWIN
                     |
       +-------------+-------------+
       |             |             |
       v             v             v
     SOH           RUL        Anomaly State
       |             |             |
       +-------------+-------------+
                     |
                     v
             Degradation Model
                     |
                     v
             Future trajectory
                     |
                     v
             Scenario Simulator
                     |
                     v
             Engineer Copilot
```

The **SOH model tells us where the battery is.**

The **RUL model tells us where it is likely to go.**

The **digital twin maintains the evolving virtual state connecting the two.**

The **scenario simulator asks what may happen under alternative operating conditions.**

The **GenAI copilot makes those model outputs accessible to an engineer without becoming the source of the numerical truth.**

---

# 9. Key Takeaways

1. **Battery degradation is multi-mechanistic.** Capacity fade and resistance growth are observable consequences of several underlying electrochemical and thermal processes.

2. **SOH is not a universal scalar.** Capacity-based SOH is chosen because it is measurable, interpretable and directly supported by the selected aging data, but resistance, thermal behavior and power capability should also be represented.

3. **RUL depends on EOL definition.** There is no meaningful RUL without specifying what constitutes end of life.

4. **RUL prediction must use partial history.** Otherwise, the model can accidentally use future information and produce unrealistic results.

5. **Cell-level data is valuable but insufficient for claiming an EV-pack twin.** It provides a strong foundation for cell-level prognostics but does not capture pack topology, thermal interactions, BMS behavior and cell-to-cell variability.

6. **The pack should therefore be an explicit extension.** Build and validate the cell twin first, then construct a virtual multi-cell pack and study aggregation/weakest-cell effects.

7. **The GenAI layer should not replace the scientific models.** The LLM should orchestrate tools, explain validated outputs and communicate uncertainty.

8. **A credible digital twin is more than an ML model.** It combines state estimation, temporal evolution, prediction, uncertainty and scenario simulation around a persistent virtual representation of the physical asset.


---

# 10. References and Further Reading

Recorded so the domain claims in this primer are traceable. Verify access dates when citing in the final report.

**Primary dataset documentation**

* **NASA PCoE Battery Data Set** — B. Saha and K. Goebel, NASA Ames Prognostics Data Repository, NASA Ames Research Center.
  * The canonical description of the cells, the CC-CV cycling protocol, the EIS measurements, and the 2 Ah → 1.4 Ah experimental EOL criterion.
  * Treat as the authority for anything protocol-related; this primer's protocol numbers are to be confirmed against it in Phase 2.

* **Oxford Battery Degradation Dataset 1** — C. Birkl, University of Oxford, ORA repository.
  * Pouch cells, controlled temperature, Artemis urban drive-cycle discharge, periodic characterisation.
  * Used for external validation only — different chemistry, format and protocol from NASA.

* **CALCE Battery Research Data** — Center for Advanced Life Cycle Engineering, University of Maryland.
  * Optional later extension; broader range of chemistries, formats and cycling profiles.

**Early prediction and feature engineering**

* **Severson et al. (2019), "Data-driven prediction of battery cycle life before capacity degradation", *Nature Energy*.**
  * Source of the ΔQ(V) feature family and the standard for leakage-safe early-life prediction.
  * The single most relevant methodological reference for Phases 6 and 8.

**Degradation mechanisms and curve shape**

* **Attia et al. (2022), "Knees in lithium-ion battery aging trajectories", *Journal of the Electrochemical Society*.**
  * Taxonomy of knee behaviour and the proposed mechanisms behind it; source of the §2.6 framing.

* A general review of Li-ion degradation mechanisms (SEI growth, lithium plating, LAM/LLI) — for the physics summarised in §2.1–2.4.

* A survey of data-driven RUL prediction for Li-ion batteries — consult primarily for its tables of health indicators, rather than for model architectures.

**Note on capacity regeneration:** the self-recovery effect described in §2.7 is widely documented in
studies using the NASA dataset. Cite a specific source before making quantitative claims about jump
magnitude; the description here is qualitative and should be verified empirically during Phase 4 EDA.