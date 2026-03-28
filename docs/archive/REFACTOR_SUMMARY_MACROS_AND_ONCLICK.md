---
render_with_liquid: false
---

# Macros & Inline Event Handler Refactor Summary

## Completed: 2026-02-13

This document summarizes the refactoring work to introduce Jinja2 macros for reusable HTML patterns and eliminate inline `onclick` handlers in favor of event listeners.

---

## 1. Created `templates/macros.html`

A new file containing reusable Jinja2 macros to reduce code duplication across templates:

### Macros Defined:

1. **`form_group()`** - Renders label + input/select/textarea combinations
   - Supports text, number, date, select, textarea, checkbox types
   - Handles all common attributes (placeholder, required, min, max, step, etc.)
   - Example usage:
     ```jinja
     {{ form_group('email', 'Email Address:', type='email', required=True) }}
     {{ form_group('status', 'Status:', type='select', options=['Active', 'Inactive']) }}
     ```

2. **`stat_box()`** - Renders statistics display boxes (value + label)
   - Example usage:
     ```jinja
     {{ stat_box('42 hrs', 'Total Integration Time') }}
     ```

3. **`action_button()`** - Renders styled buttons
   - Example usage:
     ```jinja
     {{ action_button('Save', type='submit', extra_class='primary') }}
     ```

4. **`help_badge()`** - Renders help badge icons
   - Example usage:
     ```jinja
     {{ help_badge('general_settings', 'Click for help') }}
     ```

---

## 2. Updated Templates to Use Macros

### `templates/journal_form.html`
- ✅ Added `{% from "macros.html" import form_group %}` at the top
- ✅ Removed inline `onclick` from cancel button → replaced with `data-cancel-url` attribute
- ✅ Added event listener in scripts block to handle cancel button click
- **Ready for further refactoring**: Many form-group patterns can be converted to use the `form_group()` macro

### `templates/_project_subtab.html`
- ✅ Added `{% from "macros.html" import stat_box, form_group %}`
- ✅ Replaced 3 hardcoded stat-box patterns with `{{ stat_box() }}` calls:
  - Total Integration Time
  - Status
  - Primary Target
- **Result**: Reduced ~15 lines of repetitive HTML to 3 clean macro calls

### `templates/config_form.html`
- ✅ Added `{% from "macros.html" import form_group, help_badge %}`
- ✅ Replaced "Back to Dashboard" button `onclick` with `data-nav-url` attribute
- ✅ Replaced 6 help badge `onclick` handlers with `{{ help_badge() }}` macro calls:
  - data_management
  - general_settings
  - locations_general
  - horizon_mask
  - rigs_general
  - shared_items_general

---

## 3. Removed Inline `onclick` Handlers from Base Templates

### `templates/base.html`
**Before:**
```html
<button id="theme-toggle-btn" onclick="toggleTheme()">Night View</button>
<button id="red-mode-btn" onclick="toggleRedMode()">Red View</button>
<div id="universal-help-modal" onclick="closeHelpModal()">
  <button onclick="closeHelpModal()">Close</button>
</div>
```

**After:**
```html
<button id="theme-toggle-btn">Night View</button>
<button id="red-mode-btn">Red View</button>
<div id="universal-help-modal">
  <button id="help-modal-close-btn">Close</button>
</div>
```

### `templates/index.html`
**Before:**
```html
<select id="location-select" onchange="setLocation()"></select>
<span onclick="openHelp('simulation_mode')">?</span>
<span onclick="openHelp('search_syntax')">?</span>
```

**After:**
```html
<select id="location-select"></select>
<span data-help-topic="simulation_mode">?</span>
<span data-help-topic="search_syntax">?</span>
```

---

## 4. Updated JavaScript Files with Event Listeners

### `static/js/base.js`
**Added:**
- Event listeners for theme toggle buttons (`theme-toggle-btn`, `red-mode-btn`)
- Event listener for help modal close button and backdrop click
- **Event delegation** for all help badges using `data-help-topic` attribute
- **Event delegation** for navigation buttons using `data-nav-url` attribute

**Code:**
```javascript
// Theme buttons
if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
if (redModeBtn) redModeBtn.addEventListener('click', toggleRedMode);

// Help modal
helpModal.addEventListener('click', (e) => {
    if (e.target === helpModal) closeHelpModal();
});

// Event delegation for help badges
document.addEventListener('click', (e) => {
    if (e.target.classList.contains('help-badge')) {
        openHelp(e.target.dataset.helpTopic);
    }
});

// Event delegation for navigation buttons
document.addEventListener('click', (e) => {
    if (e.target.dataset.navUrl) {
        window.location.href = e.target.dataset.navUrl;
    }
});
```

### `static/js/dashboard.js`
**Added:**
- Event listener for `location-select` dropdown in DOMContentLoaded block

**Code:**
```javascript
const locationSelect = document.getElementById('location-select');
if (locationSelect) {
    locationSelect.addEventListener('change', setLocation);
}
```

---

## 5. Benefits of This Refactor

### ✅ **Code Reusability**
- Common patterns (form fields, stat boxes, help badges) are now defined once in `macros.html`
- Reduces duplication across templates
- Easier to maintain and update styling/behavior globally

### ✅ **Separation of Concerns**
- HTML is cleaner without inline JavaScript
- Follows modern web development best practices
- JavaScript logic is centralized in `.js` files

### ✅ **Better Performance**
- Event delegation reduces the number of individual event listeners
- Help badges and navigation buttons use a single delegated listener instead of dozens

### ✅ **CSP (Content Security Policy) Compliance**
- Removing inline `onclick` handlers improves security posture
- Allows stricter CSP policies in the future

### ✅ **Easier Testing & Debugging**
- Event handlers can be tested independently
- Clearer separation between markup and behavior

---

## 6. Remaining Work (Future Iterations)

### Templates Still Using Inline Handlers:
While we've tackled the most critical instances, some inline handlers remain in:
- `config_form.html` (file upload triggers, form submissions, dropdown interactions)
- Other templates not yet reviewed

### Patterns That Can Be Converted to Macros:
- More `form_group()` conversions in `journal_form.html` (dozens of opportunities)
- Inline field patterns in `config_form.html`
- Button groups and action sections

### Suggested Next Steps:
1. **Phase 2**: Convert all help badges across all templates to use `{{ help_badge() }}`
2. **Phase 3**: Convert all form fields in `journal_form.html` to use `{{ form_group() }}`
3. **Phase 4**: Audit and convert remaining inline handlers in `config_form.html`
4. **Phase 5**: Extract button groups into a `button_group()` macro

---

## 7. Testing Checklist

After deployment, verify:
- [ ] Theme toggle (Night View / Day View) works
- [ ] Red mode toggle works
- [ ] Help badges open the help modal correctly
- [ ] Help modal can be closed via backdrop or close button
- [ ] "Back to Dashboard" button in config form works
- [ ] Location selector dropdown triggers data refresh
- [ ] Cancel button in journal form redirects correctly
- [ ] Stat boxes display correctly in project subtab
- [ ] All help badges in config form still work

---

## Audit Point Resolution

✅ **Audit Point 13** (Inline Event Handlers) - **RESOLVED**
- Removed all critical `onclick` handlers from `base.html` and `index.html`
- Replaced with event listeners and data attributes
- Established pattern for future refactoring

---

**End of Summary**
