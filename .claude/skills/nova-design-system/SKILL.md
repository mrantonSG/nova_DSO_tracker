---
name: nova-design-system
description: >
  Design system reference for Nova DSO Tracker (nova_DSO_tracker). Use this skill
  whenever working on any UI, CSS, styling, or visual change to the Nova DSO Tracker
  codebase. Triggers on: adding new UI elements, fixing visual bugs, creating new
  pages or templates, adjusting colors or typography, implementing dark mode support,
  styling buttons or form elements, working on the dashboard, config page, journal,
  graph view, or any other Nova template. Also use when Claude Code asks "what style
  should this look like?" or when reviewing a PR that touches CSS or HTML templates.
  This skill contains the complete design language, color tokens, component patterns,
  and hard-won lessons from the v5.1 facelift — always consult it before writing
  any CSS or HTML for this project.
---

# Nova DSO Tracker — Design System

This skill documents the complete visual design language established in the v5.1
facelift. Read this before writing any CSS, HTML, or styling-related JS for Nova.

---

## Core Principles

1. **Touch CSS only** — never modify JS logic, event handlers, Python routes, or
   element IDs/classes that are referenced in JS files.
2. **Inline `<style>` blocks win** — always check templates for inline `<style>`
   tags before editing external CSS files. They override external files regardless
   of specificity. Known locations: `templates/_journal_section.html`.
3. **JS-locked selectors** — never rename or remove IDs/classes listed in the
   Locked Selectors section below.
4. **CSS variables everywhere** — never hardcode colors in new rules. Always use
   the design tokens defined below.

---

## File Structure

```
static/css/
  tokens.css          ← sizing, spacing, font-size variables (editable)
  base.css            ← main stylesheet, color system, global typography
  nova-theme.css      ← facelift overrides (font imports, badge styles, etc.)
  dashboard.css       ← dashboard-specific styles
  config_form.css     ← configuration page
  graph_view.css      ← object detail / chart page
  journal_form.css    ← journal section
  objects_section.css ← objects tab
  heatmap_section.css ← heatmap visualization
  mobile.css          ← mobile/responsive (do not touch for desktop work)
  login.css           ← login page

templates/
  base.html           ← base layout, <head>, header HTML
  macros.html         ← reusable components (stat_box, tab_button, etc.)
  _journal_section.html ← contains inline <style> block — check here first
  graph_view.html     ← Notes & Framing buttons defined here
```

**Vendor files** — never edit anything under `venv/`.

---

## Color Tokens

### Light Mode (`:root`)

```css
--primary-color:   #83b4c5;   /* Nova brand teal — the signature color */
--primary-hover:   #6a9eb0;
--primary-light:   #a8cdd8;
--primary-bg:      #eaf4f8;   /* teal tint background for highlights */

/* Backgrounds */
--bg-primary:      #f2f0ed;   /* warm off-white page background */
--bg-secondary:    #ffffff;   /* surface / card */
--bg-tertiary:     #f7f5f2;   /* surface-alt / hover background */

/* Borders */
--border-color:    #e5e2dc;
--border-light:    #eeebe5;

/* Text */
--text-primary:    #141414;
--text-secondary:  #4a4a4a;
--text-muted:      #888888;
--text-faint:      #aaaaaa;

/* Shadows */
--shadow-sm:       0 1px 3px rgba(0,0,0,0.08);
--shadow-md:       0 2px 8px rgba(0,0,0,0.08);

/* Row hover */
--hover-bg:        #f0f7fa;
```

### Dark Mode (`[data-theme="dark"]`)

```css
--primary-color:   #8ec8da;   /* brighter in dark for readability */
--primary-bg:      rgba(142,200,218,0.1);

/* Backgrounds */
--bg-primary:      #0f1118;
--bg-secondary:    #161b24;
--bg-tertiary:     #1c2230;

/* Borders */
--border-color:    rgba(255,255,255,0.07);
--border-light:    rgba(255,255,255,0.04);

/* Text */
--text-primary:    rgba(255,255,255,0.92);
--text-secondary:  rgba(255,255,255,0.55);
--text-muted:      rgba(255,255,255,0.30);
--text-faint:      rgba(255,255,255,0.18);

/* Shadows */
--shadow-sm:       0 1px 3px rgba(0,0,0,0.30);
--shadow-md:       0 2px 8px rgba(0,0,0,0.40);

/* Row hover */
--hover-bg:        rgba(122,175,192,0.06);
```

### Dark Mode Toggle Mechanism
- Toggle button: `id="theme-toggle"` — **do not rename**
- Attribute set on `<html>`: `data-theme="dark"`
- Persisted via `localStorage.getItem('theme')`
- Blocking script in `<head>` applies theme before render (no flash)
- Custom event `themeChanged` fires on toggle

---

## Typography

### Font Stack
```css
font-family: 'DM Sans', 'Roboto', system-ui, -apple-system, sans-serif;
--font-mono: 'DM Mono', 'Courier New', monospace;
```
DM Sans is loaded via Google Fonts import at top of `nova-theme.css`.
Roboto (self-hosted WOFF2) remains as fallback.

### Type Scale (from `tokens.css`)
```
--font-size-xs:   11px   ← micro-labels, badges, tooltips
--font-size-2xs:  13px   ← table data, body
--font-size-sm:   14px   ← secondary UI
--font-size-md:   15px   ← page titles
--font-size-base: 16px
```

### Key Rules
- **Column headers**: 10px, `font-weight:700`, `text-transform:uppercase`,
  `letter-spacing:0.09em`, `color:var(--text-faint)`
- **Sub-labels** (units under headers): 9px, `font-weight:400`, `display:block`
- **Numeric data** in tables: `font-family:var(--font-mono)`, 12px
- **Object IDs** (NGC, SH, IC codes): DM Mono, 12px, `font-weight:600`
- **Section micro-labels**: 10px, uppercase, `letter-spacing:0.09em`

---

## Button System

Three semantic variants. Use **only** these — never create one-off button styles.

### Primary
```css
background: var(--primary-color);
color: #ffffff;
border: none;
padding: 6px 14px;
border-radius: 6px;
font-size: 12px;
font-weight: 600;
font-family: 'DM Sans', sans-serif;
cursor: pointer;
box-shadow: var(--shadow-sm);
```
Hover: `filter: brightness(0.9)`
Dark extra: `box-shadow: 0 0 14px rgba(142,200,218,0.2)`

**Use for**: Save, Edit, Create, primary actions.
**Class**: `.inline-button` (base), or any primary `<button>`

### Ghost / Secondary
```css
background: transparent;
color: var(--text-secondary, #4a4a4a);
border: 1px solid var(--border-color, #d0cdc8);  /* hardcoded fallback required */
padding: 6px 14px;
border-radius: 6px;
font-size: 12px;
font-weight: 500;
cursor: pointer;
```
Hover: `background: var(--bg-tertiary)`
Dark: `color: rgba(255,255,255,0.55)`, `border-color: rgba(255,255,255,0.15)`

**Use for**: Cancel, Duplicate, Download, Show Framing, secondary actions.
**Class**: `.inline-button-ghost`

> ⚠️ Always include the hardcoded fallback on `border-color` — the CSS variable
> sometimes fails to resolve in certain contexts.

### Danger
```css
background: rgba(180,60,60,0.08);
color: #a04040;
border: 1px solid rgba(180,60,60,0.18);
padding: 6px 14px;
border-radius: 6px;
font-size: 12px;
font-weight: 600;
cursor: pointer;
```
Hover: `background: rgba(180,60,60,0.15)`
Dark: `background: rgba(200,80,80,0.1)`, `color: #d06060`

**Use for**: Delete actions only.
**Class**: `.inline-button-danger`

### Nova AI (AI entry points only)
```css
display: inline-flex;
align-items: center;
gap: 6px;
background: var(--primary-bg);
color: var(--primary-hover);
border: 1px solid rgba(131, 180, 197, 0.35);
padding: 6px 14px;
border-radius: 6px;
font-size: 12px;
font-weight: 600;
font-family: 'DM Sans', sans-serif;
cursor: pointer;
```
Hover: `background: rgba(131,180,197,0.22); border-color: rgba(131,180,197,0.55)`
Dark: `color: var(--primary-color); border-color: rgba(142,200,218,0.25)`

**Use for**: Every AI-powered action in the UI — "Ask Nova", and any future AI entry points.
**Class**: `.btn-nova-ai`
**Always include the star icon inside the button:**
```html
<svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
  <path d="M8 1L9.5 6H14.5L10.5 9L12 14L8 11L4 14L5.5 9L1.5 6H6.5L8 1Z" fill="currentColor"/>
</svg>
```
**JS loading states** — use `innerHTML` not `textContent` to preserve the icon:
- Analyzing: `btn.innerHTML = 'Analyzing session…'` (icon strips during this phase — acceptable)
- Writing: `btn.innerHTML = 'Nova writing… <span style="font-size:10px;font-weight:700;background:var(--primary-color);color:#fff;border-radius:10px;padding:1px 6px;">N words</span>'`
- Reset: `btn.innerHTML = '<svg .../>  Ask Nova'`

> ⚠️ Safari applies `font-style: italic` to inline SVGs. If the star skews during loading,
> add `style="font-style:normal"` directly to the SVG element.

### Remove Filter (special case)
Ghost style but signals "reset" — same ghost CSS, position far right of tab bar.

### Border Radius
All buttons: `border-radius: 6px` — **never** pill shapes (no `border-radius > 8px`).
The global `.inline-button` rule uses `var(--radius-lg)` = 12px which creates pills.
Always override with `border-radius: 6px` on new button rules.

---

## Tab Bar

### Main Tabs (dashboard, detail page)
```css
/* Container */
height: 44px;
background: var(--bg-secondary);
border-bottom: 1px solid var(--border-color);
display: flex;
align-items: center;
padding: 0 28px;

/* Each tab */
height: 44px;
display: inline-flex;
align-items: center;
padding: 0 14px;
font-size: 13px;
font-weight: 500;
color: var(--text-muted);
border-bottom: 2px solid transparent;
margin-bottom: -1px;
white-space: nowrap;

/* Active tab — uses ::after pseudo-element */
.tab-button.active {
  border-bottom: none;
  position: relative;
  color: var(--primary-color);
  font-weight: 600;
}
.tab-button.active::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 2px;
  background: var(--primary-color);
  border-radius: 2px 2px 0 0;  /* the refined rounded cap */
}
```

> The rounded cap on the active indicator (`border-radius: 2px 2px 0 0`) is
> intentional brand polish. Preserve it on all tab bars.

### Dark Mode Tabs
Old dark mode had a box/border around active tab — this is wrong. The `::after`
underline applies in both modes. Ensure dark mode has:
```css
[data-theme="dark"] .tab-button.active {
  border: none;
  color: var(--primary-color);
}
```

### Design Principle
**Rounded corners belong on cards/objects, not on structural chrome.**
The header, status strip, and tab bars are full-width surfaces — they stay flat.
Only contained elements (tables, cards, badges) get `border-radius`.

---

## Data Table

### Altitude Badges
The altitude column shows a pill-shaped badge, not a full-cell background color.
The `<td>` has a transparent background; a `<span>` inside carries the color.

```css
/* High altitude (≥75°) */
.alt-high span {
  background: rgba(131,180,197,0.52);
  color: #1d6a80;
  border: 1px solid rgba(131,180,197,0.65);
}

/* Medium altitude (≥60°) */
.alt-med span {
  background: rgba(131,180,197,0.32);
  color: #2d7d95;
  border: 1px solid rgba(131,180,197,0.45);
}

/* Low / no badge — plain text, no background */

/* All badges share: */
display: inline-flex;
align-items: center;
justify-content: center;
min-width: 62px;
padding: 3px 9px;
border-radius: 5px;
font-family: var(--font-mono);
font-size: 12px;
font-weight: 500;
```

Dark mode:
```css
[data-theme="dark"] .alt-high span {
  background: rgba(142,200,218,0.28);
  color: #8ec8da;
  border: 1px solid rgba(142,200,218,0.42);
}
[data-theme="dark"] .alt-med span {
  background: rgba(142,200,218,0.16);
  color: #6db8cc;
  border: 1px solid rgba(142,200,218,0.28);
}
```

### Trend Arrows
```css
.trend-up   { color: #2a9060; }
.trend-down { color: var(--text-muted); }

[data-theme="dark"] .trend-up   { color: #6dcab0; }
[data-theme="dark"] .trend-down { color: rgba(255,255,255,0.25); }
```

### Threshold Row (yellow → teal)
Objects near the observability limit get a row highlight. The old yellow color
was updated to teal to match the brand:
```css
/* the .highlight class or equivalent */
background-color: var(--primary-bg);
color: var(--primary-color);
```

### Remove Filter Button
Ghost style (see Button System). NOT primary/teal. It's a reset action.

---

## Dropdown / Select Elements

All `<select>` elements get a custom SVG arrow. **Always include `background-size`**
or the arrow tiles across the entire field (known bug):

```css
select {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23aaa'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 10px center;
  background-size: 10px 6px;  /* ← REQUIRED, never omit */
  padding-right: 28px;
  appearance: none;
}

[data-theme="dark"] select {
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%23888'/%3E%3C/svg%3E");
  background-size: 10px 6px;  /* ← REQUIRED here too */
}
```

---

## Header Structure

```
┌─ .app-header ──────────────────────────────────────────────┐
│  .header-left                    .header-right             │
│    .brand-group (Nova logo)        header_actions block     │
│    .header-divider                 #theme-toggle            │
│    .injected-title                 .user-menu               │
└────────────────────────────────────────────────────────────┘
┌─ .status-strip ────────────────────────────────────────────┐
│  LOCATION · LOCAL TIME · MOON · DUSK/DAWN                  │
│  [date input] [simulation toggle] [?]        UPDATE in Xs  │
└────────────────────────────────────────────────────────────┘
```

The header has a subtle gradient line below it via `::after` pseudo-element.
In dark mode, a star-field texture appears via `::before` radial gradients.

---

## Known Gotchas

### 1. Inline `<style>` blocks override external CSS
`templates/_journal_section.html` contains an inline `<style>` block with
`.log-analysis-tab-btn` styles. Any external CSS changes to that selector
will have zero effect. Always edit the inline block directly for those rules.

**Diagnosis**: If your CSS change has no effect, run this in DevTools console:
```js
const el = document.querySelector('YOUR_SELECTOR');
const sheets = [...document.styleSheets];
// Find which stylesheet (or null = inline <style>) is winning
```
`file: null` in the result means an inline `<style>` block is the source.

### 2. Flex containers override button width
When buttons are inside a flex container, `width` and `min-width` are ignored
in favor of flex sizing. Use `grid-template-columns: repeat(N, 1fr)` on the
container instead to force equal widths.

### 3. JS-generated buttons need class + no inline styles
Dynamically created buttons (e.g. in `graph_view_chart.js`) often set
`element.style.backgroundColor` which overrides CSS classes. To restyle them:
- Add the appropriate class (`inline-button-ghost`, `inline-button-danger`)
- Remove all `element.style.*` property assignments

### 4. Alt badge HTML structure
The altitude badge requires a `<span>` inside the `<td>`. If the template
renders raw text in the `<td>` with no wrapping span, the CSS pill style
cannot be applied. The span must be added in the Jinja template:
```html
<td class="alt-cell {{ alt_class }}">
  <span>{{ altitude }}°</span>
</td>
```
Never build HTML strings in Python and pass them to templates — Jinja will
escape them and render raw `<span>` text.

### 5. Inspiration card badges
Two badges overlap in the card image corner because both use `bottom + right`
positioning. Fix: type badge stays `bottom: 8px; right: 8px`, attribution
badge moves to `bottom: 8px; left: 8px`.

---

## Locked JS Selectors (never rename or remove)

These IDs and classes are referenced in JavaScript files. Renaming them
breaks functionality silently.

**Critical IDs:**
`theme-toggle`, `update-notification`, `flash-message-container`,
`sim-mode-toggle`, `sim-date-input`, `altitudeChartCanvas`, `chart-loading`,
`dso-table-wrapper`, `journal-table-wrapper`, `outlook-wrapper`,
`object-filter-*` series, all modal IDs

**Critical Classes:**
`.flash-message`, `.tab-button`, `.tab-content`, `.view-button`,
`.is-visible`, `.loading`, `.hidden`, `.highlight`, `.active`,
`.dropdown-btn`, `.dropdown-content`, `.show`,
`.clickable-row`, `.status-strip`, `.status-item`,
`.data-table`, `.data-body`, `.outlook-table`, `.outlook-body`,
`.sort-indicator`, `.filter-row`,
`.session-data-row`, `.view-mode`, `.project-tab-content`,
`.objects-list`, `.object-grid-container`, `.bulk-select-checkbox`

**Critical Data Attributes:**
`data-tab`, `data-action`, `data-column-key`, `data-journal-column-key`,
`data-date`, `data-col`, `data-id`, `data-ptab`, `data-type`

---

## Design Philosophy

- **Objects are rounded, surfaces are flat.** Tables, cards, badges get
  `border-radius`. Headers, tab bars, status strips do not.
- **The active tab indicator** uses `border-radius: 2px 2px 0 0` on a
  `::after` pseudo-element — subtle polish, preserve it.
- **Brand color** is `#83b4c5` teal. It owns the altitude column, the active
  tab, the Configuration button, and the Nova wordmark. Do not replace it
  with a different hue — the palette can be refined but not recolored.
- **Altitude column is the hero element** — it draws the eye first. Badge
  saturation at ~50% opacity is the calibrated balance between "visible brand
  color" and "doesn't dominate the data".
- **Ghost button on border**: always use hardcoded `#d0cdc8` fallback alongside
  the CSS variable — the variable resolves inconsistently in some contexts.

---

## CC Bulk Translation Prompt Template

Use this template when requesting bulk translations for Nova DSO Tracker UI strings:

### Leave untranslated
- Leave untranslated: SQM, RA, Dec, FWHM, RMS, PHD2, ASIAIR, NINA, OAG, Darks, Flats, Bias, SIMBAD, HFR, Nova, DSO
- "Nova" is a PROPER NAME (the app's AI companion), not the Latin word for "new". Never translate it. This applies to all strings containing Nova: "Ask Nova", "Nova writing", "Nova error", "Nova DSO Tracker", etc.
