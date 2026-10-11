# AeroTwin AI: The Self-Optimizing Aircraft Fleet (teammate's concept doc)

> **Source:** written by one of Shamit's teammates (author: *add name*); pasted into the project on 2026-10-10 ~8:20 PM ET.
> Reformatted for readability (a few bullet lists run inline; HTML entities, the equation rendering and the stack table fixed). Content unchanged.
> The wording is the author's. How this fits with the Meridian plan: see [`../MERGE-PLAN.md`](../MERGE-PLAN.md).

**Tagline:** Predict the failure. Simulate the future. Prevent the disruption.

**Hackathon Theme:** AI/ML: Put models to work: agents, assistants, and data pipelines that take real tasks off someone's plate.
*(Per this doc; verify the exact track wording on Devpost.)*

## 1. Project Overview

AeroTwin AI is an AI-powered aircraft digital twin platform that predicts component degradation, simulates future aircraft health, and dynamically optimizes maintenance schedules across an airline fleet.

The platform creates a continuously updated virtual representation of every aircraft, combining sensor telemetry, flight cycles, maintenance history, aircraft locations, flight schedules, spare-part inventory, and technician availability.

Machine learning models identify abnormal component behavior and estimate Remaining Useful Life (RUL). A multi-agent AI system translates these predictions into coordinated maintenance recommendations, while a simulation engine evaluates alternative decisions before they are implemented.

Unlike conventional predictive maintenance dashboards, AeroTwin AI connects failure prediction, digital twin simulation, resource-aware scheduling, and agentic task orchestration in one closed-loop system.

Its goal is to help airlines reduce avoidable downtime, improve fleet availability, and make better maintenance decisions while keeping authorized professionals in control of safety-critical actions.

## 2. The Problem

Aircraft maintenance is a high-stakes operational coordination problem.

- **Unexpected failures:** Component faults can lead to aircraft-on-ground (AOG) events, flight delays, cancellations, and costly recovery operations.
- **Static maintenance schedules:** Fixed schedules may not reflect the actual degradation of individual components.
- **Distributed resources:** Aircraft, maintenance facilities, spare parts, and qualified technicians may be located in different cities.
- **Operational conflicts:** Maintenance must be coordinated with flight rotations, ground time, facility capacity, and parts availability.
- **Reactive decision-making:** Maintenance planners may need to respond quickly to changing aircraft conditions and resource constraints.

The fundamental challenge is that aircraft health, flight operations, maintenance resources, and logistics are interconnected, yet decisions often span multiple systems.

AeroTwin AI addresses this by creating a shared digital representation of fleet health and using predictive intelligence, optimization, and AI agents to coordinate maintenance planning.

## 3. Core Features

### 3.1 Predictive Aircraft Health Intelligence

Use machine learning to detect abnormal component behavior and estimate degradation.

**Inputs:**
- Engine temperature, pressure, vibration, and other sensor signals.
- Flight hours and operating cycles.
- Historical maintenance records.
- Operating conditions and component history.

**ML capabilities:**
- Anomaly detection using Isolation Forest or autoencoders.
- Remaining Useful Life estimation using XGBoost, Random Forest, or LSTM models.
- Component-level health scoring and risk ranking.
- Confidence estimates and data-quality checks.

**Example output:** Aircraft A17 shows an abnormal engine temperature trend. The model estimates a remaining useful life of 18-25 operating cycles, with uncertainty displayed to the maintenance planner.

This is an illustrative prototype prediction, not an actual aircraft assessment or authorization to operate.

### 3.2 Interactive Aircraft Digital Twin

Create a virtual representation of each aircraft and its major maintainable components.

Features include:
- Interactive 3D aircraft visualization.
- Clickable engine, landing gear, hydraulic, and avionics components.
- Component health indicators and degradation trends.
- Flight hours, accumulated cycles, and maintenance history.
- Predicted future component condition.
- A timeline of changes in aircraft health.

The digital twin should update as new telemetry arrives and as simulated flights, faults, and maintenance events occur.

### 3.3 Fleet-Aware Predictive Maintenance Scheduling

Optimize maintenance decisions using the current status of every aircraft.

The scheduler considers:
- Aircraft location and upcoming flight rotations.
- Component health and maintenance requirements.
- Available maintenance facilities and ground windows.
- Technician availability and qualifications.
- Spare-part inventory and delivery estimates.
- Maintenance deadlines and operational constraints.

The system recommends feasible maintenance locations and time windows while minimizing avoidable downtime, operational disruption, and logistics costs.

For example, it may recommend scheduling an inspection at a suitable maintenance hub during an available ground window rather than waiting for a later planned maintenance visit.

All recommendations must respect approved maintenance requirements and operational constraints.

### 3.4 Multi-Agent AI Maintenance Orchestration

Deploy specialized AI agents that work together to complete maintenance-planning tasks.

**Health Agent**
- Analyzes ML predictions and component health.
- Prioritizes anomalies and emerging risks.
- Retrieves relevant maintenance records.

**Scheduling Agent**
- Finds feasible maintenance windows.
- Coordinates maintenance with aircraft rotations.
- Calls a constraint-based optimization solver.

**Logistics Agent**
- Checks spare-part availability.
- Matches technician skills to maintenance requirements.
- Identifies resource shortages and repositioning needs.

**Operations Agent**
- Evaluates the effect of maintenance on flight schedules.
- Compares alternative fleet configurations.
- Estimates operational consequences.

**Coordinator Agent**
- Combines agent outputs into one actionable plan.
- Identifies conflicting recommendations.
- Produces an explanation and evidence trail.
- Prepares proposed work orders for human approval.

Agents should use structured tools and shared data rather than relying on free-form LLM reasoning for safety-critical decisions or numerical optimization.

### 3.5 What-If Simulation and Counterfactual Planning

Simulate alternative maintenance decisions before implementing them.

Example scenarios:
- What if maintenance occurs now instead of after several additional flights?
- What if the aircraft needs to be serviced at another airport?
- What if a required spare part is delayed by 12 hours?
- What if two aircraft need the same limited technician resource?
- What if a maintenance facility becomes unavailable?

The simulation engine evaluates alternative plans and compares projected downtime, cost, delays, resource utilization, and fleet readiness.

This is the digital twin's central advantage: evaluate the consequences of decisions before acting on the real fleet.

### 3.6 Explainable AI Maintenance Copilot

Provide a conversational assistant for maintenance planners and fleet managers.

Example queries:
- Which aircraft are most likely to require maintenance in the next 72 hours?
- Why is aircraft A17 prioritized over A12?
- Find the best feasible maintenance windows for tomorrow.
- Which parts and technicians are required for these maintenance jobs?
- What changes if a maintenance facility becomes unavailable?
- Compare the current schedule with the optimized schedule.

The copilot should explain recommendations using structured data and approved documentation, identify missing information, and generate auditable maintenance proposals.

## 4. The Killer Feature: Self-Adjusting Maintenance Plans

AeroTwin AI continuously replans as aircraft health and operational conditions change.

**Example workflow**
1. **Detect:** A simulated aircraft develops an abnormal engine temperature pattern.
2. **Predict:** The ML model identifies an anomaly and estimates the degradation trajectory.
3. **Investigate:** The health agent retrieves supporting sensor trends and relevant maintenance history.
4. **Simulate:** The digital twin evaluates alternative maintenance locations and windows.
5. **Optimize:** The scheduling engine checks aircraft availability, technician capacity, spare parts, and operational constraints.
6. **Recommend:** The coordinator produces a prioritized maintenance plan with supporting evidence.
7. **Replan:** A simulated parts delay triggers a new optimization run and revised recommendation.
8. **Approve:** An authorized maintenance professional reviews and approves the proposed action.

This creates a closed-loop workflow connecting data, prediction, simulation, decision-making, and replanning.

## 5. Technical Architecture

The system consists of six major layers.

**Layer 1: Data Ingestion.** Collect and normalize sensor telemetry, flight hours and cycles, aircraft locations, flight schedules, maintenance history, technician availability, and spare-part inventory. For the prototype, use a combination of public degradation datasets and synthetic operational data.

**Layer 2: AI/ML Prediction Engine.** Time-series feature extraction, anomaly detection, degradation forecasting, Remaining Useful Life estimation, confidence and data-quality assessment. Suggested models: Isolation Forest for anomaly detection; XGBoost or Random Forest for tabular prediction; LSTM models as an optional time-series extension.

**Layer 3: Digital Twin State Engine.** Maintain the current and simulated state of each aircraft: aircraft and component health, flight cycles and operating history, maintenance status, resource availability, simulated events and future state transitions.

**Layer 4: Simulation Engine.** Simulate aircraft flight rotations, ground windows, maintenance duration, component degradation, spare-part delivery delays, and technician and facility constraints. A discrete-event simulation approach is suitable for the prototype.

**Layer 5: Agent Orchestration and Optimization.** Use tool-calling agents to coordinate tasks and an optimization solver to generate feasible schedules. The agents interpret goals, gather data, invoke tools, and explain decisions. The optimizer handles mathematical constraints and objective functions.

**Layer 6: User Interface.** Fleet overview and airport map, interactive aircraft digital twin, component health analytics, maintenance scheduling timeline, AI-generated recommendations, what-if scenario comparisons, human approval and audit history.

## 6. Recommended Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Frontend | React or Next.js | Interactive dashboard |
| UI styling | Tailwind CSS | Responsive interface |
| 3D visualization | Three.js / React Three Fiber | Aircraft digital twin |
| Backend | Python and FastAPI | APIs and business logic |
| ML | scikit-learn, XGBoost, PyTorch | Prediction and anomaly detection |
| Simulation | SimPy or custom simulator | Future-state modeling |
| Optimization | Google OR-Tools | Constraint-based scheduling |
| Agent orchestration | LangGraph or tool-calling LLMs | Multi-agent workflows |
| Database | PostgreSQL | Fleet state and maintenance records |
| Time-series storage | TimescaleDB, optional | Telemetry history |
| Real-time updates | WebSockets or polling | Dashboard synchronization |

## 7. Data Sources and Prototype Strategy

Access to proprietary airline telemetry is not necessary for a convincing hackathon prototype.

**Public degradation data.** NASA's C-MAPSS turbofan engine degradation dataset can support Remaining Useful Life estimation and degradation-model evaluation. It is useful for the predictive component but does not provide a complete airline fleet scheduling dataset.

**Synthetic fleet data.** Generate realistic simulated records for aircraft IDs and airport locations; flight schedules and operating cycles; component health states; maintenance requirements; technicians and qualifications; maintenance facilities and capacities; spare-part inventory and delivery times.

**Synthetic telemetry.** Generate normal sensor patterns and controlled anomalies. Include configurable temperature, pressure, and vibration trends.

**Scenario injection.** Allow a user to trigger events such as a new component anomaly, an accelerated degradation trend, a delayed spare part, an unavailable technician, or a closed maintenance facility. These events allow the entire pipeline to be demonstrated without connecting to real aircraft systems.

## 8. Optimization Formulation

The maintenance scheduler can be formulated as a constrained optimization problem.

**Objective.** Minimize a weighted combination of maintenance downtime, flight disruption, logistics and repositioning costs, resource utilization penalties, delays in required maintenance, and schedule changes. A simplified objective is:

```
min ( α·D + β·C + γ·L + δ·P )
```

Where D represents projected downtime, C represents operational disruption, L represents logistics costs, and P represents penalties for infeasible or late scheduling. The coefficients represent configurable priorities.

**Constraints.** The scheduler must respect approved maintenance deadlines, aircraft availability, facility capacity, technician qualifications and availability, spare-part availability, maintenance duration, and flight schedules and ground windows.

Hard safety and regulatory constraints must never be traded away to improve a numerical objective.

The optimization engine should return a feasible schedule or explicitly report that no feasible schedule was found.

## 9. Evaluation Metrics

Evaluate both predictive performance and operational planning quality.

| Metric | Evaluation |
|---|---|
| Fault detection | Precision, recall, and false-positive rate |
| RUL prediction | Mean Absolute Error and Root Mean Squared Error |
| Downtime | Baseline versus optimized simulated downtime |
| Flight disruption | Number of simulated flights affected |
| Scheduling feasibility | Constraint violations and feasibility rate |
| Resource utilization | Technician and facility utilization |
| Replanning speed | Time to produce a revised feasible plan |
| Agent effectiveness | Percentage of recommendations with complete evidence |
| Explainability | Ability to trace recommendations to source data and constraints |

Report prototype results honestly, including the baseline, dataset, assumptions, and limitations. Simulated improvements must not be presented as verified airline savings or real-world safety improvements.

## 10. Dashboard Design

- **Fleet Overview:** number of aircraft; aircraft requiring attention; predicted maintenance events; aircraft currently in maintenance; fleet readiness indicators; airport locations and aircraft status.
- **Aircraft Digital Twin:** interactive 3D aircraft; component health indicators; sensor trends and anomaly alerts; predicted degradation; flight and maintenance history.
- **Maintenance Planning Board:** maintenance jobs and time windows; aircraft ground availability; technician and facility allocations; parts availability; baseline versus optimized schedule.
- **Simulation Laboratory:** fault injection controls; spare-part delay controls; facility availability controls; scenario comparison charts; updated schedules and operational impact estimates.
- **AI Maintenance Copilot:** natural-language queries; evidence-backed recommendations; proposed maintenance work orders; explanations of scheduling trade-offs; human review and approval workflow.

## 11. Hackathon Demonstration Plan

The demonstration should tell one connected story.

1. **Introduce the fleet.** Display 20-50 simulated aircraft across multiple airports. Show their schedules, component health, and maintenance resources.
2. **Trigger an anomaly.** Inject an abnormal engine telemetry pattern. Show the ML model detecting the anomaly and estimating degradation.
3. **Activate the agents.** Demonstrate the health, scheduling, and logistics agents gathering information and invoking their tools.
4. **Generate a maintenance plan.** Show the optimized maintenance location and time window, along with the constraints and evidence that influenced the decision.
5. **Introduce a disruption.** Simulate a delayed part or unavailable maintenance facility. Trigger replanning and show the revised schedule.
6. **Demonstrate measurable impact.** Compare the baseline and optimized plans using simulated downtime, affected flights, logistics costs, and resource utilization.

The demonstration should prove that the system predicts, decides, acts through tools, simulates, and adapts.

## 12. Implementation Priorities

**Must-have features**
- Synthetic fleet and airport data.
- One working anomaly detection or RUL model.
- Component-level digital twin state.
- Constraint-based maintenance scheduler.
- Two or three agents using real tools.
- One disruption scenario with automatic replanning.
- Baseline versus optimized results.

**Should-have features**
- Clickable 3D aircraft.
- Live-looking telemetry.
- Fleet map.
- Maintenance timeline.
- Explainable recommendations.
- Scenario comparison charts.

**Stretch goals**
- Weather-aware disruption scenarios.
- Advanced component-level degradation models.
- Retrieval-augmented maintenance-document assistant.
- Technician skill matching.
- Spare-part repositioning.
- Multiple simultaneous faults and fleet-wide optimization.

Recommended build order: prediction model, synthetic fleet simulator, scheduling optimizer, agent workflow, and dashboard polish.

A complete end-to-end demonstration is more valuable than many disconnected features.

## 13. Differentiation and Innovation

Predictive maintenance and digital twins are established research areas. The project's contribution is integrating them into one operational decision-making loop.

AeroTwin AI combines four capabilities:
1. **Predictive intelligence:** Estimate when components may require attention.
2. **Digital twin simulation:** Evaluate future aircraft and fleet states.
3. **Agentic execution:** Delegate investigation, scheduling, and logistics tasks to specialized tool-using agents.
4. **Prescriptive optimization:** Generate feasible maintenance plans and revise them as conditions change.

The central differentiator is not simply predicting a fault. It is translating predictions into coordinated decisions while accounting for the real operational constraints of a distributed fleet.

## 14. Expected Impact

If validated with representative operational data, AeroTwin AI could help airlines: reduce avoidable aircraft downtime; improve maintenance resource utilization; reduce unnecessary operational disruptions; improve coordination between maintenance and flight operations; identify emerging maintenance risks earlier; make maintenance planning more transparent and explainable; reduce manual effort spent coordinating data across systems.

These are intended benefits, not guaranteed results. Demonstrate them through controlled simulations and clearly documented assumptions.

The platform is a decision-support prototype. It does not replace approved maintenance procedures, airworthiness assessments, certified engineering judgment, or authorized personnel.

## 15. Final Pitch

**AeroTwin AI: The Self-Optimizing Aircraft Fleet**

Every unexpected aircraft maintenance event can trigger a chain reaction: aircraft grounded at the wrong airport, unavailable spare parts, disrupted flight rotations, and maintenance teams forced to make time-critical decisions with incomplete information. The challenge is not just identifying which component might fail. It is deciding what to do about it across an entire fleet.

AeroTwin AI is an AI-powered digital twin that transforms aircraft maintenance from reactive troubleshooting into proactive, simulation-driven fleet orchestration.

We create a dynamic virtual representation of each aircraft, combining sensor telemetry, component degradation, flight cycles, maintenance history, aircraft locations, and operational schedules. Machine learning models identify abnormal behavior and estimate remaining useful life, while a constraint-based optimization engine determines feasible maintenance windows and resource allocations.

Our multi-agent AI system turns these predictions into coordinated action. Specialized agents analyze aircraft health, find maintenance opportunities, verify parts and technician availability, and evaluate operational consequences. A simulation engine tests alternative decisions before they are recommended to maintenance professionals.

What makes AeroTwin different is its ability to continuously replan. When a component shows signs of degradation, a spare part is delayed, or a maintenance facility becomes unavailable, the system simulates the consequences and generates a revised plan.

Instead of simply predicting what could go wrong, AeroTwin helps answer the more valuable questions: What should we do next? Where should we do it? When is the best feasible window? What will happen if circumstances change?

Our goal is to demonstrate a future in which aircraft maintenance decisions are predictive, explainable, resource-aware, and continuously optimized, helping airlines reduce avoidable downtime, improve fleet availability, and make better use of maintenance resources while keeping authorized professionals in control of all safety-critical decisions.

*AeroTwin AI doesn't just predict the future of an aircraft. It helps teams plan for it.*
