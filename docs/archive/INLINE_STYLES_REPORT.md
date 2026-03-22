# Inline Styles Report: HTML Templates with Hardcoded Colors

This report documents all inline styles containing hardcoded hex colors found in HTML templates. These should be refactored to use CSS classes with CSS variables or converted to use `var()` syntax if inline styles must remain.

## Summary Statistics

- **Total templates with inline styles**: 12
- **Total inline style instances**: 150+
- **Priority files** (most instances): _journal_section.html, _objects_section.html, graph_view.html, config_form.html

---

## 1. templates/_journal_section.html (50+ instances)

### High Priority Colors
- `#555` (gray text) - 4 instances
- `#666` (gray text) - 2 instances
- `#777` (gray text) - 1 instance
- `#ddd` (borders) - 5 instances
- `#eee` (borders/dividers) - 3 instances

### Semantic Button Colors (Consider extracting to CSS classes)
- `#ffc107` (warning yellow - Edit button) - 2 instances → `var(--warning-color)`
- `#17a2b8` (info cyan - Duplicate/Download buttons) - 4 instances → `var(--info-color)`
- `#dc3545` (danger red - Delete button) - 2 instances → `var(--danger-color)`
- `#28a745` (success green - Create/Save buttons) - 2 instances → `var(--success-color)`
- `#6c757d` (gray - Cancel button) - 3 instances → `var(--accent-gray-medium)`
- `#007bff` (blue - Print button) - 2 instances → `var(--info-color-alt2)`
- `#83b4c5` (primary - ASIAIR button) - 1 instance → `var(--primary-color)`

### Text & Background Colors
- `#495057` (section headers) - 3 instances → `var(--accent-gray-text)`
- `#c0392b` (delete checkbox label) - 1 instance
- `#6c757d` (muted text) - 1 instance → `var(--accent-gray-medium)`
- `#fff` (white backgrounds) - 2 instances → `var(--bg-white)`

### Border Colors
- `#dee2e6` (table borders) - 1 instance → `var(--border-light-alt2)`

### Recommendations:
1. Create semantic button classes: `.btn-edit`, `.btn-delete`, `.btn-save`, `.btn-cancel`, `.btn-duplicate`, `.btn-download`
2. Create section header class: `.section-header`
3. Extract form styles to dedicated classes
4. Consider creating utility classes for common inline patterns

---

## 2. templates/_objects_section.html (30+ instances)

### Form Input & Container Colors
- `#ccc` (input borders) - 8 instances → `var(--border-medium-alt)`
- `#555` (label text, muted elements) - 5 instances → `var(--text-secondary)`
- `#fff` (white backgrounds) - 2 instances → `var(--bg-white)`
- `#e0e0e0` (container borders) - 2 instances → `var(--border-light-alt)`

### Badge & Status Colors
- `#bdc3c7` (disabled badge) - 1 instance
- `#aaa` (disabled text) - 1 instance → `var(--text-light)`

### Semantic Button Colors
- `#27ae60` (enable green) - 1 instance → `var(--success-color-alt)`
- `#f39c12` (disable orange) - 1 instance → `var(--warning-color-alt)`
- `#83b4c5` (close button) - 1 instance → `var(--primary-color)`
- `#778899` (cancel slate) - 1 instance → `var(--accent-slate)`

### Inspiration Section Colors
- `#e9ecef` (selection bar background) - 1 instance → `var(--border-light-alt3)`

### Dark Mode Overrides Present
- Note: This file has dark mode CSS overrides in objects_section.css

### Recommendations:
1. Create form input utility classes: `.form-input`, `.form-textarea`
2. Extract inspiration container styles to CSS class
3. Create badge utility classes: `.badge-disabled`, `.badge-status`
4. Consolidate button action styles

---

## 3. templates/graph_view.html (25+ instances)

### Component Colors
- `#83b4c5` (primary/brand elements) - 2 instances → `var(--primary-color)`
- `#333` (dark text) - 5 instances → `var(--text-primary)`
- `#555` (medium text) - 3 instances → `var(--text-secondary)`
- `#666` (muted text) - 3 instances → `var(--text-tertiary)`
- `#ddd` (borders, disabled elements) - 3 instances → `var(--border-medium)`
- `#ccc` (borders) - 2 instances → `var(--border-medium-alt)`

### Special Purpose Colors
- `#7f8c8d` (back button) - 1 instance → `var(--accent-gray)`
- `#444` (labels) - 1 instance → `var(--text-dark-alt)`

### Recommendations:
1. Extract iFrame styles to CSS class
2. Create utility classes for rotation controls
3. Consolidate coordinate display styles
4. Consider extracting framing toolbar styles

---

## 4. templates/config_form.html (20+ instances)

### Table & Loading States
- `#555` (loading/empty state text) - 5 instances → `var(--text-secondary)`
- `#333` (headers, primary text) - 2 instances → `var(--text-primary)`
- `#666` (character counter, helper text) - 4 instances → `var(--text-tertiary)`
- `#ddd` (image borders, demo borders) - 2 instances → `var(--border-medium)`
- `#ccc` (disabled button backgrounds) - 2 instances → `var(--border-medium-alt)`

### Special Colors
- `#007bff` (importing message) - 1 instance → `var(--info-color-alt2)`
- `#f5f5f5` (rig detail separator) - 1 instance

### Recommendations:
1. Create loading state classes: `.loading-text`, `.empty-state`
2. Extract character counter to utility class
3. Create table placeholder styles
4. Consider flash message component system

---

## 5. templates/mobile_mosaic_copy.html (10 instances)

### Mobile-Specific Colors
- `#888` (close button) - 1 instance → `var(--text-muted)`
- `#eef2f5` (info box background) - 1 instance
- `#555` (info text) - 1 instance → `var(--text-secondary)`
- `#666` (label text) - 1 instance → `var(--text-tertiary)`
- `#ccc` (textarea border) - 1 instance → `var(--border-medium-alt)`
- `#83b4c5` (copy button) - 1 instance → `var(--primary-color)`
- `#27ae60` (success feedback) - 1 instance → `var(--success-color-alt)`

### Recommendations:
1. Extract mobile modal styles to mobile.css
2. Create mobile button variants
3. Consider toast/feedback component

---

## 6. templates/journal_report.html (8 instances)

### Report-Specific Colors
- `#555` (caption text) - 1 instance → `var(--text-secondary)`
- `#f8f9fa` (placeholder background) - 1 instance → `var(--bg-light)`
- `#ccc` (placeholder text) - 1 instance → `var(--border-medium-alt)`
- `#212529` (detail headers) - 2 instances
- `#eee` (border separator) - 2 instances → `var(--border-light)`

### Recommendations:
1. Create print-specific stylesheet
2. Extract report layout to CSS classes
3. Consider report component library

---

## 7. templates/project_report.html (6 instances)

### Report Stats Colors
- `#f8f9fa` (stat box background) - 3 instances → `var(--bg-light)`
- `#eee` (stat box borders) - 3 instances → `var(--border-light)`
- `#333` (stat values) - 2 instances → `var(--text-primary)`
- `#777` (stat labels) - 2 instances → `var(--text-quaternary)`
- `#f39c12` (rating stars) - 1 instance → `var(--warning-color-alt)`
- `#999` (footer text) - 1 instance → `var(--text-muted-alt)`

### Recommendations:
1. Create report stat component styles
2. Extract table styles to shared report CSS
3. Consider print media queries

---

## 8. templates/base.html (2 instances)

### Header Colors
- `#83b4c5` (login/logout links) - 2 instances → `var(--primary-color)`

### Recommendations:
1. Extract to `.header-link` class
2. Consider auth component styles

---

## 9. templates/_project_subtab.html (6 instances)

### Content Colors
- `#777` (no-project message, no-sessions message) - 2 instances → `var(--text-quaternary)`
- `#28a745` (save button) - 1 instance → `var(--success-color)`
- `#6c757d` (cancel button) - 1 instance → `var(--accent-gray-medium)`
- `#c0392b` (delete checkbox) - 1 instance

### Recommendations:
1. Create empty state component
2. Extract form button styles
3. Consider subtab-specific stylesheet

---

## 10. templates/mobile_location.html (1 instance)

### Mobile Text Colors
- `#555` (description text) - 1 instance → `var(--text-secondary)`

### Recommendations:
1. Add to mobile.css typography

---

## 11. templates/_heatmap_section.html (3 instances)

### Progress & Loading Colors
- `#eee` (progress bar background) - 1 instance → `var(--bg-medium-alt)`
- `#6795a4` (progress bar fill) - 1 instance → `var(--primary-dark)`
- `#666` (loading text) - 1 instance → `var(--text-tertiary)`

### Recommendations:
1. Create progress bar component
2. Extract to heatmap_section.css

---

## 12. templates/_inspiration_section.html (6 instances)

### Inspiration Gallery Colors
- `#333` (modal title, helper text) - 2 instances → `var(--text-primary)`
- `#555` (helper text, list items) - 2 instances → `var(--text-secondary)`
- `#ccc` (button borders) - 1 instance → `var(--border-medium-alt)`
- `#666` (loading indicator, offline text) - 2 instances → `var(--text-tertiary)`
- `#f8f9fa` (offline message background) - 1 instance → `var(--bg-light)`
- `#999` (offline/no-image text) - 2 instances → `var(--text-muted-alt)`
- `#777` (empty state text) - 1 instance → `var(--text-quaternary)`

### Recommendations:
1. Extract modal content styles to CSS
2. Create tile component styles
3. Add loading/offline state classes

---

## 13. templates/journal_form.html (2 instances)

### Form Colors
- `#333` (form title) - 1 instance → `var(--text-primary)`
- `#555` (helper text) - 1 instance → `var(--text-secondary)`

### Recommendations:
1. Extract to journal_form.css
2. Create form title component

---

## 14. templates/index.html (7 instances)

### Dashboard Colors
- `#ca0e0e` (simulated mode indicator) - 1 instance → `var(--danger-color-darker)`
- `#aaa` (separator) - 1 instance → `var(--text-light)`
- `#83b4c5` (timer value) - 1 instance → `var(--primary-color)`
- `#444` (checkbox label) - 1 instance → `var(--text-dark-alt)`
- `#6795a4` (loading message, progress bar) - 3 instances → `var(--primary-dark)`
- `#eee` (progress bar background) - 1 instance → `var(--bg-medium-alt)`
- `#666` (loading counter) - 1 instance → `var(--text-tertiary)`
- `#ddd` (loading box border) - 1 instance → `var(--border-medium)`
- `#fff` (modal background) - 1 instance → `var(--bg-white)`
- `#28a745` (save button) - 1 instance → `var(--success-color)`
- `#6c757d` (cancel button) - 1 instance → `var(--accent-gray-medium)`

### Recommendations:
1. Extract loading overlay to component
2. Create modal component styles
3. Add dashboard-specific utilities to dashboard.css

---

## Overall Recommendations

### Immediate Actions (Quick Wins)
1. **Button Classes**: Create semantic button classes in base.css
   ```css
   .btn-edit { background-color: var(--warning-color); }
   .btn-delete { background-color: var(--danger-color); }
   .btn-save { background-color: var(--success-color); }
   .btn-cancel { background-color: var(--accent-gray-medium); }
   .btn-duplicate { background-color: var(--info-color); }
   ```

2. **Text Utility Classes**: Add to base.css
   ```css
   .text-muted { color: var(--text-tertiary); }
   .text-secondary { color: var(--text-secondary); }
   .text-primary { color: var(--text-primary); }
   ```

3. **Loading Components**: Standardize loading overlays
4. **Empty State Component**: Standardize "no data" displays

### Medium Priority
1. Extract modal styles to dedicated CSS
2. Create form component library
3. Standardize report layouts
4. Create badge/pill component system

### Long-Term Refactoring
1. Move all inline styles to CSS classes
2. Create component-based architecture
3. Build design system documentation
4. Consider CSS-in-JS for dynamic styles

### Template Editing Strategy
For each template, consider:
1. Can this style be extracted to a CSS class?
2. If it must be inline (dynamic values), can it use CSS variables?
3. Is this pattern repeated? If so, create a component.

### Example Refactoring Pattern

**Before:**
```html
<button style="background-color:#28a745;">Save</button>
```

**After (Option 1 - CSS Class):**
```html
<button class="btn-save">Save</button>
```

**After (Option 2 - CSS Variable):**
```html
<button style="background-color:var(--success-color);">Save</button>
```

---

## Notes

- **Dark Mode**: Many templates have corresponding dark mode CSS. When refactoring, ensure dark mode compatibility.
- **Print Styles**: Report templates may need print-specific CSS.
- **Mobile Styles**: Mobile templates should reference mobile.css exclusively.
- **Dynamic Styles**: Some inline styles are dynamically generated (e.g., from Python). These should use CSS variables if colors are hardcoded.

## Next Steps

1. ✅ Define CSS variables in base.css (COMPLETED)
2. ✅ Replace hardcoded colors in all CSS files (COMPLETED)
3. ✅ Replace z-index values with CSS variables (COMPLETED)
4. ⏳ Create semantic button classes (RECOMMENDED)
5. ⏳ Refactor high-priority templates (_journal_section.html, _objects_section.html)
6. ⏳ Create component library
7. ⏳ Document design system

---

*Generated: 2026-02-13*
*Total CSS files refactored: 10*
*Total templates requiring attention: 14*
