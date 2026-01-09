# AwesomeRolePlayingGame Technical Specification

## Overview
This document defines the technical specification for a multi-agent, LLM-driven role-playing game (RPG) framework. The framework emphasizes consistent world-building, hierarchical plot control, configurable rules/attributes, and durable save/load support.

## Goals
- Support multi-round, narrative-focused gameplay via coordinated agents.
- Prevent world-building contradictions by enforcing upfront scaffolding and controlled incremental detail.
- Maintain plot coherence through hierarchical story structure (Arc/Scene).
- Provide configurable attribute and resolution systems with optional visibility.
- Enable robust save/load of all game state components.

## Agent Architecture
### Core Agents
1. **StateManagerAgent**
   - **Responsibilities**: Read/write all state files; maintain schema versions; provide snapshots to other agents.
   - **Inputs**: Current game state request, partial updates (`delta`).
   - **Outputs**: Persisted state, resolved snapshot, conflict reports.

2. **WorldbuilderAgent**
   - **Responsibilities**: Generate world scaffolding at initialization; incrementally expand world details during play.
   - **Inputs**: World theme/setting prompt, current world state, plot triggers.
   - **Outputs**: World deltas that extend (never overwrite) existing scaffolding.

3. **PlotDirectorAgent**
   - **Responsibilities**: Maintain Arc/Scene hierarchy; determine plot progression and branch selection.
   - **Inputs**: Current plot state, user action, world context.
   - **Outputs**: Scene transitions, Arc completions, new Arc scaffolding requests.

4. **RulesEngineAgent**
   - **Responsibilities**: Evaluate checks based on character/environment attributes; interpret results.
   - **Inputs**: User action, attributes, environment modifiers, visibility settings.
   - **Outputs**: Resolution result (explicit/implicit), modifiers, outcome metadata.

5. **NarratorAgent**
   - **Responsibilities**: Produce narrative output while adhering to world/plot constraints.
   - **Inputs**: World state, plot state, rules results, user action.
   - **Outputs**: Narration text and suggested prompts for next user input.

### Agent Interaction Flow
1. **StateManagerAgent** provides the current snapshot.
2. **PlotDirectorAgent** determines plot direction (Arc/Scene) from user action.
3. **RulesEngineAgent** evaluates checks and returns outcomes.
4. **NarratorAgent** writes the narrative response and options.
5. **StateManagerAgent** persists world/plot/rules deltas and new snapshot.

## Data Model and File Layout
### World Data (`world/`)
- `world/overview.json`: Era, core world rules, global themes.
- `world/regions.json`: Continents/regions, geography skeleton.
- `world/nations.json`: Nations/factions, governance, alliances (scaffold).
- `world/tech_society.json`: Baseline tech, economy, culture, military.

**Constraints**:
- Initialization generates skeleton-only values.
- Incremental updates may only fill empty or undefined fields.
- Conflicts are rejected and returned to `PlotDirectorAgent`.

### Plot Data (`plot/`)
- `plot/arc_index.json`: List of all Arcs, status, prerequisites.
- `plot/arc_current.json`: Current Arc summary, conflicts, branches.
- `plot/scene_current.json`: Current Scene node, location, participants.

**Rules**:
- Every turn must be associated with exactly one Scene.
- Arc completion triggers creation of a new Arc scaffold.

### Rules Data (`rules/`)
- `rules/attributes.json`: Attribute definitions (world-dependent).
- `rules/checks.json`: Check rules (dice, thresholds, probabilities).
- `rules/visibility.json`: `explicit` or `implicit` display mode.

### Save Data (`saves/<save_id>/`)
- `saves/<save_id>/world/*`
- `saves/<save_id>/plot/*`
- `saves/<save_id>/rules/*`
- `saves/<save_id>/state.json`: Current Arc/Scene, character snapshots, schema versions.

## Initialization Flow
1. Collect user-selected theme/setting.
2. `WorldbuilderAgent` generates world scaffolding files.
3. `PlotDirectorAgent` generates initial Arc/Scene scaffolding.
4. `RulesEngineAgent` initializes attributes and checks from setting.
5. `StateManagerAgent` persists all initial files and returns snapshot.

## Incremental World Expansion
- Triggered when a scene references a region, faction, or system that lacks detail.
- `WorldbuilderAgent` receives the minimal context and fills only missing detail.
- Expanded data is versioned and appended as deltas.

## Plot Progression Rules
- Scene transitions occur when the user action resolves key objectives.
- Arc ends when conditions in `arc_current.json` are satisfied.
- New Arc scaffolding is generated based on current world state and player actions.

## Rules and Resolution
- Checks may be deterministic or probabilistic depending on `checks.json`.
- The output format depends on visibility settings:
  - **Explicit**: show numbers, rolls, thresholds, and outcome.
  - **Implicit**: only narrative consequence is provided.

## Conflict Handling
- When a new delta conflicts with existing world or plot data:
  - The delta is rejected.
  - A conflict report is returned to `PlotDirectorAgent` for adjustment.
  - Narration must remain consistent with the existing state.

## Versioning
- Each file includes `schema_version` and `last_updated` metadata.
- Save files include a `spec_version` to ensure compatibility.
