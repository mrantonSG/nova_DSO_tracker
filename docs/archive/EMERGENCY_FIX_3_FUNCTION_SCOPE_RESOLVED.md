# Emergency Fix #3: Function Scope Error Resolved

**Date:** 2026-02-13
**Issue:** Uncaught ReferenceError: showTab is not defined
**Root Cause:** graph_view_chart.js trying to expose functions that don't exist in its scope

---

## Error Message

```
graph_view_chart.js?v=kb_fix_v4:2191 Uncaught ReferenceError: showTab is not defined
    at graph_view_chart.js?v=kb_fix_v4:2191:22
graph_view.js:204 [GRAPH_VIEW] openFramingAssistant function not available!
```

---

## Root Cause Analysis

### The Problem

**File Loading Order:**
1. `graph_view_chart.js` loads first (line 419 of template)
2. `graph_view.js` loads second (line 420 of template)

**Incorrect Function Exposures in graph_view_chart.js (Lines 2191-2213):**

```javascript
// WRONG - These functions don't exist in graph_view_chart.js!
window.showTab = showTab;  // ❌ Defined in graph_view.js, not here
window.showProjectSubTab = showProjectSubTab;  // ❌ Defined in graph_view.js, not here
```

**Where Functions Are Actually Defined:**

| Function | Defined In | Exposed In (Before Fix) |
|----------|-----------|------------------------|
| `showTab` | graph_view.js:419 | ✅ graph_view.js:419<br>❌ graph_view_chart.js:2191 |
| `showProjectSubTab` | graph_view.js:53 | ✅ graph_view.js:53<br>❌ graph_view_chart.js:2193 |
| `toggleProjectSubTabEdit` | graph_view.js:84 | ✅ graph_view.js:84 |
| `changeView` | graph_view_chart.js:1753 | ✅ graph_view_chart.js:2192 |
| `saveProject` | graph_view_chart.js:1813 | ✅ graph_view_chart.js:2194 |
| `openFramingAssistant` | graph_view_chart.js:1105 | ✅ graph_view_chart.js:2195 |

**The Error:**
When graph_view_chart.js executed line 2191:
```javascript
window.showTab = showTab;  // ReferenceError: showTab is not defined
```

The variable `showTab` doesn't exist in graph_view_chart.js's scope because it's defined in graph_view.js (which hasn't loaded yet).

---

## Fix Applied

### Removed Incorrect Window Assignments ✅

**File:** `static/js/graph_view_chart.js`
**Lines 2191-2193:** Removed references to functions not defined in this file

**BEFORE (Lines 2190-2194):**
```javascript
// Expose functions needed by event delegation in graph_view.js
window.showTab = showTab;  // ❌ DOESN'T EXIST
window.changeView = changeView;
window.showProjectSubTab = showProjectSubTab;  // ❌ DOESN'T EXIST
window.saveProject = saveProject;
```

**AFTER (Lines 2190-2192):**
```javascript
// Expose functions needed by event delegation in graph_view.js
// NOTE: showTab, showProjectSubTab, toggleProjectSubTabEdit are defined in graph_view.js, not here
window.changeView = changeView;
window.saveProject = saveProject;
```

### Verified Correct Exposures ✅

**Functions Exposed in graph_view_chart.js (CORRECT):**
- ✅ `window.changeView` - Defined at line 1753
- ✅ `window.saveProject` - Defined at line 1813
- ✅ `window.openFramingAssistant` - Defined at line 1105
- ✅ `window.closeFramingAssistant` - Defined at line 1271
- ✅ `window.applyLockToObject` - EXISTS
- ✅ `window.toggleGeoBelt` - Defined at line 1281
- ✅ `window.flipFraming90` - Defined at line 1279
- ✅ `window.copyFramingUrl` - EXISTS
- ✅ `window.saveFramingToDB` - EXISTS
- ✅ `window.updateFramingChart` - EXISTS
- ✅ `window.updateFovVsObjectLabel` - EXISTS
- ✅ `window.onRotationInput` - Defined at line 967
- ✅ `window.setSurvey` - Defined at line 1663
- ✅ `window.updateImageAdjustments` - EXISTS
- ✅ `window.copyRaDec` - EXISTS
- ✅ `window.resetFovCenterToObject` - EXISTS
- ✅ `window.nudgeFov` - EXISTS
- ✅ `window.copyAsiairMosaic` - EXISTS
- ✅ `window.setLocation` - EXISTS
- ✅ `window.selectSuggestedDate` - EXISTS
- ✅ `window.openInStellarium` - EXISTS

**Functions Exposed in graph_view.js (CORRECT):**
- ✅ `window.showTab` - Defined and exposed at line 419
- ✅ `window.showProjectSubTab` - Defined and exposed at line 53
- ✅ `window.toggleProjectSubTabEdit` - Defined and exposed at line 84
- ✅ `window.loadTrixContentEdit` - Defined and exposed at line 68

---

## Why This Now Works

### Before Fix (BROKEN):
```
1. Browser loads graph_view_chart.js
2. graph_view_chart.js tries: window.showTab = showTab
3. ❌ ReferenceError: showTab is not defined (doesn't exist in this file)
4. ❌ Script execution STOPS (all subsequent window assignments fail)
5. ❌ NO functions exposed to window
6. ❌ ALL buttons fail
```

### After Fix (WORKING):
```
1. Browser loads graph_view_chart.js
2. graph_view_chart.js exposes: changeView, saveProject, openFramingAssistant, etc.
3. ✅ All assignments succeed (functions exist in this file)
4. Browser loads graph_view.js
5. graph_view.js exposes: showTab, showProjectSubTab, toggleProjectSubTabEdit
6. ✅ All assignments succeed (functions exist in this file)
7. ✅ ALL functions available on window
8. ✅ ALL buttons work
```

---

## Expected Behavior After Fix

### Day/Month/Year Buttons ✅
```
[GRAPH_VIEW] Click action triggered: change-view
[GRAPH_VIEW] change-view: day function exists: function
Chart switches to day view
```

### Save Notes Button ✅
```
[GRAPH_VIEW] Click action triggered: save-project
[GRAPH_VIEW] save-project, function exists: function
Project notes saved
```

### Framing Assistant ✅
```
[GRAPH_VIEW] Click action triggered: open-framing-assistant
[GRAPH_VIEW] open-framing-assistant, function exists: function
Modal opens
```

### Close Framing Modal ✅
```
[GRAPH_VIEW] Click action triggered: close-framing-assistant
[GRAPH_VIEW] close-framing-assistant
Modal closes
```

---

## Files Modified

### static/js/graph_view_chart.js
- ✅ Removed `window.showTab = showTab;` (line 2191)
- ✅ Removed `window.showProjectSubTab = showProjectSubTab;` (line 2193)
- ✅ Added comment explaining why those functions aren't exposed here

---

## Testing Instructions

### 1. Hard Refresh Browser
```bash
# Clear cache completely
Cmd+Shift+R (Mac) / Ctrl+F5 (Windows/Linux)
```

### 2. Open DevTools Console

### 3. Verify No ReferenceErrors
- ✅ No "showTab is not defined" errors
- ✅ No "Uncaught ReferenceError" messages
- ✅ Clean console on page load

### 4. Test All Buttons
- Day/Month/Year view buttons
- Save Notes button
- Tab navigation (Chart/Framing/Opportunities/Journal/SIMBAD)
- Framing Assistant open/close
- All other interactive elements

### 5. Watch Console Logs
Every button click should show:
```
[GRAPH_VIEW] Click action triggered: <action>
[GRAPH_VIEW] <action>: <params> function exists: function
```

---

## Technical Explanation

### Why defer Attribute Matters

**Script Loading with defer:**
1. HTML parsing continues (not blocked)
2. Scripts download in parallel
3. Scripts execute IN ORDER after DOM ready
4. graph_view_chart.js executes FIRST
5. graph_view.js executes SECOND

**Critical Rule:**
- A script can ONLY expose functions that exist in its own scope
- graph_view_chart.js CANNOT expose functions defined in graph_view.js
- Each file must expose its own functions

### Function Scope in IIFE Modules

**graph_view_chart.js:**
```javascript
(function() {
    'use strict';

    function changeView(view) { ... }  // Defined here
    function showTab(tabName) { ... }  // ❌ NOT defined here

    window.changeView = changeView;  // ✅ Works
    window.showTab = showTab;  // ❌ ReferenceError
})();
```

**graph_view.js:**
```javascript
(function() {
    'use strict';

    window.showTab = function(tabName) { ... };  // ✅ Defined AND exposed here
})();
```

---

## Summary

### Issue Fixed ✅
- ❌ ReferenceError: showTab is not defined
- ✅ Each file now only exposes functions it actually defines

### Files Modified (1)
- `static/js/graph_view_chart.js` - Removed 2 incorrect window assignments

### Lines Changed
- **Removed:** 2 lines (incorrect function exposures)
- **Added:** 1 line (explanatory comment)

### Testing Status
- ✅ Function scope verified (all exposures valid)
- ✅ Script load order correct (defer attributes present)
- ⏳ Browser testing pending (user verification needed)

---

## Ready for Testing

**Function scope error resolved.** All window assignments now reference functions that actually exist in the file's scope.

**Next Steps:**
1. Hard refresh browser (Cmd+Shift+R / Ctrl+F5)
2. Open DevTools console
3. Verify NO ReferenceErrors on page load
4. Test Day/Month/Year buttons
5. Test Save Notes button
6. Test Framing Assistant modal
7. Verify console logs show successful function calls

---

**Emergency Fix #3 Complete** ✅

**Authored by:** Claude Sonnet 4.5
**Project:** Nova DSO Tracker
**Related:** Emergency Fix #2 (Delegation Conflict)
