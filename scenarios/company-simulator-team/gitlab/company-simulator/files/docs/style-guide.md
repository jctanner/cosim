# Design System — Dark Dashboard Theme

This style guide captures the design language used in the CoSim simulator UI. Use it to bootstrap new projects with the same look and feel.

---

## Color Palette

### Backgrounds (darkest to lightest)
| Token | Hex | Usage |
|-------|-----|-------|
| `bg-deep` | `#111` | Input fields, code blocks, modal content areas |
| `bg-base` | `#1a1a2e` | Page background, cards, primary surfaces |
| `bg-sidebar` | `#121a30` | Sidebar backgrounds, secondary panels |
| `bg-header` | `#16213e` | Header bar, toolbar areas, elevated surfaces |
| `bg-hover` | `#1a1a3e` | Hover states on dark backgrounds |

### Borders & Dividers
| Token | Hex | Usage |
|-------|-----|-------|
| `border-subtle` | `#0f3460` | Section dividers, sidebar borders, toolbars |
| `border-default` | `#333` | Card borders, input borders, button borders |
| `border-hover` | `#555` | Hover state borders |

### Text
| Token | Hex | Usage |
|-------|-----|-------|
| `text-primary` | `#e0e0e0` | Body text, input text |
| `text-secondary` | `#888` | Descriptions, metadata, labels |
| `text-muted` | `#555` | Timestamps, hints, disabled text |
| `text-disabled` | `#666` | Placeholder text, empty states |
| `text-bright` | `#fff` | Bold/strong text, active items |

### Accent Colors
| Token | Hex | Usage |
|-------|-----|-------|
| `accent-primary` | `#e94560` | Primary brand color, active tabs, CTA buttons, links |
| `accent-primary-hover` | `#c0392b` | Hover state for primary accent |
| `accent-info` | `#4fc3f7` | Titles, informational highlights |
| `accent-link` | `#3498db` | Secondary links, info badges |

### Status Colors
| Token | Hex | Usage |
|-------|-----|-------|
| `status-success` | `#2ecc71` | Ready, online, success states |
| `status-warning` | `#f39c12` | Starting, waiting, pending states |
| `status-active` | `#3498db` | Responding, in-progress, active states |
| `status-error` | `#e94560` | Error, restarting, critical states |
| `status-offline` | `#666` | Offline, disabled |
| `status-disconnected` | `#444` | Disconnected, unknown |

### Activity Colors (extended status)
| Token | Hex | Usage |
|-------|-----|-------|
| `activity-docs` | `#9b59b6` | Writing/reading documents |
| `activity-code` | `#e67e22` | Committing code, git operations |
| `activity-tickets` | `#1abc9c` | Managing tickets/tasks |
| `activity-marketing` | `#e056a0` | Marketing-related |
| `activity-devops` | `#00bcd4` | Infrastructure/DevOps |

### Agent/User Colors (for avatars, name labels)
Cycle through this palette for unique per-entity colors:
```
#e94560, #f39c12, #9b59b6, #2ecc71, #1abc9c,
#e67e22, #f1c40f, #3498db, #e056a0, #00bcd4, #ff6b6b
```

---

## Typography

```css
font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
```

| Element | Size | Weight | Color | Other |
|---------|------|--------|-------|-------|
| Page title / brand | 18px | 700 | `#e94560` | — |
| Section header | 13px | 700 | `#888` | uppercase, letter-spacing: 1px |
| Card title | 14px | 700 | `#4fc3f7` | — |
| Card name (bold) | 14px | 700 | `#e0e0e0` | — |
| Body / content | 14px | 400 | `#e0e0e0` | — |
| Description | 11-12px | 400 | `#888` | line-height: 1.4 |
| Metadata / dates | 10-11px | 400 | `#555` | — |
| Tag / badge | 11px | 400 | `#888` | bg: `#111` |
| Button text | 12-13px | 600 | varies | — |
| Code / monospace | 14px | 400 | `#ccc` | `font-family: monospace` |

---

## Spacing & Sizing

| Token | Value | Usage |
|-------|-------|-------|
| Sidebar width | 200px | Left navigation panels |
| Card padding | 14px 16px | Card content padding |
| Card gap | 12px | Space between cards in a grid |
| Card border-radius | 10px | Rounded card corners |
| Button padding | 6px 12px | Small buttons |
| Button border-radius | 6px-8px | Rounded button corners |
| Input padding | 8px 12px | Form field padding |
| Input border-radius | 8px | Rounded input corners |
| Section padding | 10px 14px | Sidebar section labels |
| Modal padding | 24px | Modal content padding |
| Modal border-radius | 12px | Modal corners |

---

## Components

### Header Bar
```css
background: #16213e;
border-bottom: 1px solid #0f3460;
display: flex;
align-items: center;
```
- Brand/title on the left in accent red
- Tab buttons in the middle (text-only, bottom border highlight on active)
- Action buttons on the right

### Tab Buttons
```css
padding: 12px 20px;
font-size: 13px;
font-weight: 600;
color: #888;
border-bottom: 2px solid transparent;
background: transparent;
/* Active: */
color: #e94560;
border-bottom-color: #e94560;
```

### Sidebar
```css
width: 200px;
min-width: 200px;
background: #121a30;
border-right: 1px solid #0f3460;
overflow-y: auto;
```
- Section headers: 11px, uppercase, letter-spacing, color `#555`
- Dividers: `border-top: 1px solid #0f3460`
- List items: padding 5px 14px, hover `#1a1a3e`, active: white text + bold

### Cards
```css
background: #1a1a2e;
border: 1px solid #333;
border-radius: 10px;
padding: 14px 16px;
cursor: pointer;
transition: border-color 0.15s;
/* Hover: */
border-color: #555;
```
- Flex grid: `display: flex; flex-wrap: wrap; gap: 12px;`
- Cards can be fixed width or flex: `flex: 1 1 160px; max-width: 220px;`
- Dimmed state: `opacity: 0.6`

### Status Dots
```css
width: 8-10px;
height: 8-10px;
border-radius: 50%;
display: inline-block;
/* Animated states: */
animation: pulse 0.5-1s ease-in-out infinite;

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
```

### Tags / Badges
```css
background: #111;
color: #888;
padding: 1px 6px;
border-radius: 4px;
font-size: 11px;
display: inline-block;
/* Accent variant: */
border-left: 2px solid #3498db;
```

### Buttons — Primary (CTA)
```css
background: #e94560;
color: white;
border: none;
padding: 8px 20px;
border-radius: 8px;
font-size: 13px;
font-weight: 600;
cursor: pointer;
/* Hover: */
background: #c0392b;
/* Disabled: */
opacity: 0.5;
cursor: not-allowed;
```

### Buttons — Secondary / Ghost
```css
background: transparent;
color: #888;
border: 1px solid #333;
padding: 6px 12px;
border-radius: 6px;
font-size: 12px;
cursor: pointer;
font-weight: 600;
/* Hover: */
border-color: #e94560;
color: #e94560;
```

### Input Fields
```css
background: #1a1a2e;  /* or #111 for darker contexts */
color: #e0e0e0;
border: 1px solid #333;
padding: 8px 12px;
border-radius: 8px;
font-size: 14px;
outline: none;
/* Focus: */
border-color: #e94560;
```

### Select Dropdowns
Same as input fields. Style matches across text inputs and selects.

### Modal Overlay
```css
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal {
  background: #1a1a2e;
  border: 1px solid #333;
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
}
```
- Fixed dimensions recommended: `width: 80vw; max-width: 1000px; height: 75vh;`
- Modal title: `font-size: 16px; color: #e94560;`
- Field labels: `font-size: 12px; color: #888; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;`
- Hint text: `font-size: 11px; color: #555;`
- Status text: `font-size: 12px; color: #4fc3f7;`

### Loading Overlay
```css
position: fixed;
inset: 0;
background: rgba(0, 0, 0, 0.8);
z-index: 2000;
display: flex;
align-items: center;
justify-content: center;
flex-direction: column;
gap: 12px;

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #333;
  border-top-color: #e94560;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin { to { transform: rotate(360deg); } }
```

### Two-Pane Detail View
Used for version history, thought history, etc:
- Left panel: 200-220px, `background: #121a30`, scrollable list
- Right panel: flex: 1, `background: #111`, scrollable content
- List items: `padding: 8px 12px; border-bottom: 1px solid #1a1a2e; cursor: pointer;`
- Active item: `background: #1a1a3e; border-left: 3px solid #e94560;`

### Notice Bar (dismissable)
```css
position: fixed;
top: 0;
left: 0;
right: 0;
z-index: 999;
background: #e94560;
color: #fff;
padding: 10px 20px;
font-size: 13px;
```

---

## Layout Patterns

### Full-Page App Layout
```
body: flex column, 100vh
  header: fixed height, flex row
  main-layout: flex: 1, flex row, overflow hidden
    tab-pane: flex: 1, overflow hidden
      sidebar (200px) | main content (flex: 1)
```

### Sidebar + Content
Every tab follows this pattern:
```
[Sidebar 200px] | [Main Content flex:1]
```
- Sidebar has section headers, lists, buttons
- Main content scrolls independently

### Card Grid
```css
display: flex;
flex-wrap: wrap;
gap: 12px;
```
Cards flex to fill rows, wrap to next line.

### Tier/Group Sections
```css
.tier-header {
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #888;
  margin-bottom: 10px;
  padding-bottom: 6px;
  border-bottom: 1px solid #333;
}
```

---

## Animation

Only two animations used throughout:
- **Pulse** — status dots (breathing opacity)
- **Spin** — loading spinner (rotation)

Transitions on interactive elements: `transition: all 0.15s ease` or `transition: border-color 0.15s ease`.

---

## Design Principles

1. **Dark-first** — everything is dark navy/charcoal, light text
2. **Accent sparingly** — red (`#e94560`) only for active states, CTAs, and brand
3. **Information density** — small text, compact cards, efficient use of space
4. **Status through color** — green/yellow/blue/red dots communicate state at a glance
5. **Consistent surfaces** — three background levels create depth (deep < base < header)
6. **No decoration** — no gradients, no shadows (except modals), no icons, no images
7. **Monospace for data** — code, thinking content, and structured data use monospace
