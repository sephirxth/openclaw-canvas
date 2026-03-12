# OpenClaw Canvas - First-Principles Derivation

## Core Problem

A unified spatial interface for heterogeneous personal computing activities
(monitoring, tasks, thinking, knowledge) — what is its minimally necessary architecture?

## Axiom System

```
A1 [Fragmentation]: User activities are split across independent tools; switching has cognitive cost.
A2 [Spatial Semantics]: In a visual field, spatial proximity implies semantic relevance.
A3 [Heterogeneity]: Different content types have fundamentally different update frequencies and interaction modes.
A4 [Evolvability]: The system must accept new content types without modifying core architecture. (Goal)
```

## Necessary Mechanisms

| ID | Mechanism | Derivation | Contrapositive |
|----|-----------|------------|----------------|
| M1 | Unified Viewport | A1 -> single interface | No viewport -> still fragmented |
| M2 | Position Persistence | A2 -> positions carry meaning -> must save | No persistence -> spatial meaning destroyed |
| M3 | Heterogeneous Rendering | A3 -> each type needs own renderer + update cycle | Uniform rendering -> lowest common denominator |
| M4 | Type Registry | A4 + M3 -> pluggable node types | Hardcoded -> violates evolvability |
| M5 | Viewport Transform | A2 -> content exceeds screen -> infinite plane | No pan/zoom -> space runs out |

## Key Insight: Dual Ontology

Nodes have two natures:
- **Mirror nodes**: Data lives externally (agent status, TODO.md, cron). Canvas stores type + position + config.
- **Native nodes**: Data originates on canvas (notes, thoughts). Canvas stores type + position + content.

This distinction is architectural, not an implementation detail.

## Minimal Architecture

```
Browser:  Canvas Viewport (M1+M5) + Node Store (M2) + Node Registry (M3+M4)
Server:   Persistence (/api/canvas) + API Aggregator (/api/agents, /api/todos, /api/crons)
```

Remove any component -> system fails. Add any component -> violates Occam's razor.

## Assumptions

```
H1 (critical):  Single-user, local deployment
H2 (likely):    Browser CSS transform handles <500 nodes smoothly
H3 (stable):    Existing data sources (Gateway API, TODO.md, jobs.json) remain available
```

## Limitations

- 2D plane cannot express high-dimensional relationships
- Spatial layout requires manual arrangement (auto-layout contradicts A2)
- Native node rich-text editing is complex (defer to v0.2+)

## Evolution Path

- v0.1: Replace 3 existing services (agent-monitor + todo-dashboard + cron)
- v0.2: Native note nodes + connectors
- v0.3: PKM integration (knowledge/ files as nodes, memory timeline)
- v0.4: Luna can write to canvas via API
