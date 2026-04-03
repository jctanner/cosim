# UI Component Guide — CoSim Tab Patterns

This document defines the exact HTML/CSS patterns for building new tabs in the CoSim UI. Follow these patterns exactly to avoid layout bugs.

---

## CRITICAL: Tab Pane Placement

**Every tab pane `<div>` MUST be a direct child of `#main-layout`.** The `</div>` that closes `#main-layout` comes right before `<!-- New Session Modal -->`. If your tab pane is placed after that closing `</div>`, it will render at the bottom of the page instead of filling the viewport.

```html
<div id="main-layout">
  <div id="chat-pane" class="tab-pane active">...</div>
  <div id="docs-pane" class="tab-pane">...</div>
  <div id="gitlab-pane" class="tab-pane">...</div>
  <div id="tickets-pane" class="tab-pane">...</div>
  <div id="npcs-pane" class="tab-pane">...</div>
  <div id="events-pane" class="tab-pane">...</div>
  <div id="usage-pane" class="tab-pane">...</div>
  <div id="email-pane" class="tab-pane">...</div>
  <!-- ADD NEW TAB PANES HERE — BEFORE THE CLOSING </div> -->
</div>  <!-- This closes #main-layout -->

<!-- Modals go AFTER #main-layout -->
<!-- New Session Modal -->
```

---

## Standard Sidebar + Main Layout

Every tab follows this pattern:

### CSS
```css
#mytab-pane { padding: 0; flex-direction: row; }
#mytab-sidebar { width: 200px; min-width: 200px; background: #121a30;
                 border-right: 1px solid #0f3460;
                 display: flex; flex-direction: column; overflow-y: auto;
                 padding: 8px 0; }
#mytab-main { flex: 1; display: flex; flex-direction: column;
              overflow: hidden; min-width: 0; }
```

### HTML
```html
<div id="mytab-pane" class="tab-pane">
  <div id="mytab-sidebar">
    <div class="sidebar-section">Section Header</div>
    <!-- sidebar content -->
  </div>
  <div id="mytab-main">
    <!-- main content -->
  </div>
</div>
```

### Key Rules
- Tab pane gets `padding: 0; flex-direction: row;` in CSS
- Sidebar: fixed width (200px standard, 300px for wider content)
- Main: `flex: 1` to fill remaining space
- Both styled in CSS, NOT inline styles
- `.tab-pane.active { display: flex }` is set globally — don't override

---

## Header Tab Button

```html
<button class="header-tab" data-tab="mytab">My Tab</button>
```

Add to the header bar. The tab switching JS handles showing/hiding automatically via `data-tab` matching `id="mytab-pane"`.

### Tab Switch Handler
Add loading logic to the existing tab switch handler:
```javascript
if (target === 'mytab') loadMyTab();
```

---

## Sidebar Section Headers
```html
<div class="sidebar-section">SECTION TITLE</div>
```
CSS: 11px, uppercase, letter-spacing, color #555.

## Sidebar Dividers
```html
<hr class="sidebar-divider">
```

## Sidebar Buttons
```html
<button class="channel-btn">Item Name</button>
```
Or for action buttons:
```html
<button class="session-btn" style="width:100%">+ Add Thing</button>
```

---

## Cards (NPC/Event style)

```css
.mycard { background: #1a1a2e; border: 1px solid #333; border-radius: 10px;
          padding: 14px 16px; flex: 1 1 160px; max-width: 220px; min-width: 160px;
          transition: border-color 0.15s; cursor: pointer; }
.mycard:hover { border-color: #555; }
```

Grid container:
```css
.mycard-grid { display: flex; flex-wrap: wrap; gap: 12px; }
```

---

## Modals

Modals go OUTSIDE `#main-layout`, after the closing `</div>`.

```html
<div class="modal-overlay" id="my-modal">
  <div class="modal" style="width:80vw;max-width:800px;height:75vh;display:flex;flex-direction:column">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h2 style="margin:0">Modal Title</h2>
      <button class="modal-btn-cancel" id="my-modal-close">Close</button>
    </div>
    <div style="flex:1;min-height:0;overflow-y:auto">
      <!-- content -->
    </div>
    <div class="modal-actions">
      <button class="modal-btn-cancel">Cancel</button>
      <button class="modal-btn-primary">Save</button>
    </div>
  </div>
</div>
```

Fixed dimensions: `width: 80vw; max-width: 800-1000px; height: 75vh`.
Content scrolls inside, modal doesn't resize.

---

## Sub-Tabs (within a tab)

```html
<div style="padding:10px 20px;background:#16213e;border-bottom:1px solid #0f3460;display:flex;align-items:center;gap:8px">
  <button class="session-btn my-sub-tab active" data-my-tab="view1">View 1</button>
  <button class="session-btn my-sub-tab" data-my-tab="view2">View 2</button>
</div>
```

Active state CSS:
```css
.my-sub-tab.active { background: #e94560; border-color: #e94560; color: #fff; }
```

---

## Severity Badges

```html
<span class="event-card-severity event-sev-critical">critical</span>
```

Classes: `event-sev-critical` (red), `event-sev-high` (orange), `event-sev-medium` (yellow), `event-sev-low` (green).

---

## Status Dots

```html
<span class="npc-status-dot ready"></span>
```

Classes: `ready` (green), `starting` (yellow pulse), `responding` (blue pulse), `offline` (gray), `disconnected` (dark gray), `firing` (red pulse).

---

## Checklist for Adding a New Tab

1. [ ] Add `<button class="header-tab" data-tab="mytab">` to header
2. [ ] Add `<div id="mytab-pane" class="tab-pane">` **inside `#main-layout`**, before the closing `</div>`
3. [ ] Add CSS: `#mytab-pane { padding: 0; flex-direction: row; }` plus sidebar/main styles
4. [ ] Add tab loading to the tab switch JS handler
5. [ ] Add tab loading to `reloadAllState()` if state changes on session new/load
6. [ ] Test: tab fills full viewport height, no bottom-alignment
