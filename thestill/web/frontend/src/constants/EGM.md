# constants

## Overview

Shared constants: the navigation lists both shells render from, the fixed-surface z-index ladder, pipeline stage ordering / labels / colours mirrored from the backend queue manager, and MCP scope copy.

## Interface

- `MAIN_NAV_ITEMS`, `ADMIN_NAV_ITEMS`, `SETTINGS_NAV_ITEM`, `NavEntry`, `visibleNavItems` — navigation lists [src/constants/navigation.tsx:MAIN_NAV_ITEMS]
- `PLAYER_HEIGHT_VAR`, `abovePlayer`, `MOBILE_HEADER_HEIGHT`, `MEDIA_HOST_ATTR`, `mediaLayerZIndex`, `TRANSIENT_LAYER_Z` — spec #71 layering ladder [src/constants/layers.ts:mediaLayerZIndex]
- `STAGE_ORDER`, `STAGE_RANK`, `ENTITY_BRANCH_STAGES`, `FEED_SCOPED_STAGES`, `STAGE_LABEL`, `STAGE_LABEL_ACTIVE`, `STAGE_LABEL_FAILURE`, `STAGE_BADGE_COLOR`, `STAGE_LANE_ACCENT`, `STAGE_BUTTON_COLOR` — pipeline stage metadata [src/constants/stages.ts:STAGE_ORDER]
- `SCOPE_COPY` — labels for MCP token scopes `read`, `follows`, `pipeline` [src/constants/mcpScopes.ts:SCOPE_COPY]

## Invariants

- Navigation items are added or reordered only here; the desktop sidebar (`Layout`) and mobile drawer (`NavigationDrawer`) both render these lists [src/constants/navigation.tsx:NavEntry]
- `ADMIN_NAV_ITEMS` are operator-only; `multiUserOnly` items (Analytics, Users, Audit Log) are dropped in single-user mode [src/constants/navigation.tsx:visibleNavItems]
- `STAGE_ORDER`, `ENTITY_BRANCH_STAGES` and `FEED_SCOPED_STAGES` mirror `thestill/core/queue_manager.py` and must change with it [src/constants/stages.ts:ENTITY_BRANCH_STAGES]
- Every fixed element takes a rung from the layer table rather than an ad-hoc z-index; the media layer sits one rung above its slot's host so the video is never painted behind it [src/constants/layers.ts:mediaLayerZIndex]

## Side Effects

- None (constants and pure functions; `mediaLayerZIndex` only reads the DOM ancestry of a slot) [src/constants/layers.ts:mediaLayerZIndex]

## Verification

- Run: `cd thestill/web/frontend && npm run test:run && npm run lint`
