# 6hops design notes

Written for the UI polish pass, following the frontend-design guidance
(anthropics/skills, `skills/frontend-design`). Update this file when the direction changes.

## Brief

- **Subject:** one person's professional network, drawn as a map.
- **Audience:** one user, an engineer job-hunting, using it often and quickly.
- **Primary job:** find the warmest route to a company and know who to ask first.

## Concept: a transit map of your network

Finding a warm intro is wayfinding: you are at one station, the company is another, and
people are the stops in between. The app borrows the vocabulary of transit and road signage.
Me is the "you are here" marker, a path is a route line, people are stations, and the strength
of each connection is the weight of the line.

## Tokens

| Token | Light | Dark (night map) | Meaning |
|---|---|---|---|
| `--paper` | `#F3F5F4` | `#121A1F` | Page background: cool map paper |
| `--surface` | `#FFFFFF` | `#19232A` | Panels, inputs |
| `--ink` | `#1C2A33` | `#E4EAEC` | Text |
| `--ink-muted` | `#5D6B72` | `#93A2A8` | Secondary text |
| `--rule` | `#D3DAD8` | `#2C3A41` | Borders, idle edges |
| `--route` | `#0B6E69` | `#4CC2B8` | Paths, primary actions, focus |
| `--here` | `#D93A2B` | `#FF6A57` | Me only |
| `--person` / `--company` / `--school` | `#2E5C8A` / `#8A6A1F` / `#6B4E9B` | `#7FA9D6` / `#D6B25E` / `#B39DDB` | Node kinds |

Color encodes meaning only. Red is never used for anything but Me (errors use a darker
brick, `--danger`). Teal means "route" or "do this".

## Type

- **Overpass** (variable, SIL OFL, self-hosted). It is derived from Highway Gothic, the US
  road-sign face, which fits the wayfinding concept. One family throughout.
- Scale: 12 / 14 / 16 / 20 / 26 px. Body 15px, line-height 1.5. Headings weight 750 with slight
  negative tracking. Scores and counts use tabular figures.

## Layout

```
┌ 6hops ─ Graph  Jobs  Cold email  Study ─────────────────────── Log out ┐
├ [Find someone…]  Reach [company…] [ ] through shared employers  [Find paths]   Fit  Tidy ┤
├──────────────────────────────────────────────┬─────────────────────────┤
│ map canvas                                   │ side panel (400px)      │
│                                              │ sections split by rules │
│ legend (bottom-left)                         │                         │
└──────────────────────────────────────────────┴─────────────────────────┘
```

Everything is left-aligned. On phones the panel stacks under a shorter canvas.

## Principles

1. **One bold element: the route diagram.** Path results are drawn as a vertical transit line
   with stations. Line weight is connection strength, dashed is a former employer, dotted is a
   shared-institution hop. Each result leads with the action: "Ask Rahul for an intro to Priya".
2. **Everything else is quiet.** Flat surfaces, hairline rules between panel sections instead
   of stacked cards, no gradients, no decorative shadows.
3. **Plain copy.** Sentence case, active verbs, no all-caps labels, no dot-joined meta strings,
   no arrow glyphs appended to links. Errors say what happened and how to fix it.
4. **Quality floor.** Visible keyboard focus, reduced motion respected, readable in both
   themes, usable at 390px wide.

## Review against generic defaults

- Not cream + terracotta, not black + acid accent, not a broadsheet: cool map paper with a
  teal route line and a red "you are here" marker, which comes from transit maps.
- Removed the stacked-card look from the side panel. Cards remain only for path results,
  which are selectable items.
- Dropped the uppercase edge labels, the dot-joined count line and the "← Overview" arrows
  from the first version.
