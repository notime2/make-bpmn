---
name: make-bpmn
description: Create BPMN process diagrams from natural-language process descriptions. Use when Codex needs to turn a described business process, workflow, user journey, operational procedure, or integration scenario into a BPMN 2.0 diagram, .bpmn XML file, SVG, PNG, or when it needs to review and fix BPMN layout/readability.
---

# Make BPMN

## Overview

Create BPMN 2.0 diagrams from process descriptions and verify the visual layout before handing artifacts back to the user.

Prefer a real `.bpmn` XML file with BPMN DI coordinates when the user asks for a BPMN scheme/diagram. Use Mermaid or prose only if the user explicitly requests that format.

## Workflow

1. Extract the process model.
   - Identify participants, systems, roles, lanes, triggers, end states, tasks, decisions, exceptions, data objects, and cross-participant interactions.
   - State only material assumptions. If a missing detail changes the diagram meaning, ask. Otherwise choose a conservative default and mention it.
   - Keep the user's wording and language for labels unless they ask for another language.

2. Choose BPMN structure.
   - Use a single process with lanes for roles inside one organization.
   - Use a collaboration with participants/pools when independent organizations or external systems exchange messages.
   - Use sequence flows inside one process/pool.
   - Use message flows between different participants/pools.
   - Use gateways for real branching/merging logic. Label outgoing conditional flows, not only the gateway.
   - Add join gateways when parallel or conditional branches converge and the join matters.
   - Use text annotations for assumptions, SLA notes, business rules, or details that should not become executable steps.

3. Generate BPMN 2.0 XML.
   - Include these namespaces: `bpmn`, `bpmndi`, `dc`, and `di`.
   - Set `targetNamespace` and stable IDs.
   - For every flow node, include matching `incoming` and `outgoing` references.
   - For every `sequenceFlow`, set valid `sourceRef` and `targetRef`.
   - Put `messageFlow` elements under `collaboration`, not inside `process`.
   - Keep process IDs, lane IDs, node IDs, and DI `bpmnElement` references consistent.
   - For the main participant, always use `<bpmn:participant id="Participant_Main" name="" ... />`; keep the `name` attribute as an empty string.

4. Add BPMN DI layout.
   - Include one `bpmndi:BPMNDiagram` and `bpmndi:BPMNPlane`.
   - Add `bpmndi:BPMNShape` with `dc:Bounds` for every pool, lane, task, event, gateway, subprocess, and annotation.
   - Add `bpmndi:BPMNEdge` with `di:waypoint` for every sequence flow, message flow, association, and data association.
   - Add `bpmndi:BPMNLabel` bounds for branch labels such as yes/no, approve/reject, success/error.
   - Use a left-to-right layout. Keep lane heights stable and align nodes by lane.

5. Validate and render.
   - Parse the XML with Python before visual rendering.
   - Render with the bundled visualizer:

```bash
python3 "${CODEX_HOME:-$HOME/.codex}/skills/make-bpmn/scripts/bpmn_visualizer.py" path/to/diagram.bpmn
python3 "${CODEX_HOME:-$HOME/.codex}/skills/make-bpmn/scripts/bpmn_visualizer.py" path/to/diagram.bpmn --png --width 2400
```

   - SVG rendering has no external dependencies.
   - PNG rendering requires `cairosvg`; if it is unavailable, keep the SVG.

6. Inspect and iterate.
   - Open or inspect the rendered SVG/PNG when possible.
   - Fix overlapping arrows, unreadable labels, labels outside lanes, nodes outside pools, and edge labels placed on top of gateways or tasks.
   - Repeat rendering after layout edits until the diagram is readable.

## Layout Defaults

- Pool height: enough to contain all lanes plus vertical padding.
- Lane height: 110-160 px for simple flows, larger for annotations or multiple parallel branches.
- Task size: about 140 x 60 px.
- Event size: about 36 x 36 px.
- Gateway size: about 50 x 50 px.
- Horizontal step: 180-240 px between major nodes.
- Vertical branch spacing: at least 90 px between branch paths.
- Keep event/gateway labels outside shapes. Keep task labels inside shapes.
- Route orthogonal edges with 2-5 waypoints when straight lines would cross other elements.

## Quality Checklist

Before final response, verify:

- The diagram has exactly the participants/lanes needed by the process.
- Each start event has a clear trigger and each path reaches an end event or explicit handoff.
- Conditional branches have readable labels.
- Sequence flows do not cross pool boundaries.
- Message flows are used for cross-participant communication.
- `<bpmn:participant id="Participant_Main" ...>` has `name=""`.
- BPMN DI exists for all visible elements and edges.
- The visualizer produced an SVG, and PNG when requested or useful.
- The rendered diagram is legible at normal zoom.

## Bundled Script

Use `scripts/bpmn_visualizer.py` to render BPMN 2.0 XML into SVG and optionally PNG.

Supported visual elements include pools, horizontal lanes, start/end/intermediate events, exclusive/parallel/inclusive/event-based gateways, common tasks, call activities, subprocesses, text annotations, associations, sequence flow labels, and message flows.

Known limits: vertical pools are not fully styled, event icons are simplified, advanced subprocess markers are simplified, and custom extension styling is ignored. If an unsupported element is needed, either represent it with a supported BPMN shape plus annotation or tell the user about the limitation.
