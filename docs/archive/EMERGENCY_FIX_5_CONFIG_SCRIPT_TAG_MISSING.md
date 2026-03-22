# Emergency Fix #5: Config Form Script Tag Missing

**Date:** 2026-02-13
**Critical Issue:** config_form.js was NOT being loaded at all!

---

## Root Cause

**The `config_form.html` template was missing the script tag to load `config_form.js`!**

This means:
- ❌ No event delegation code was running
- ❌ Buttons had data-action attributes but no listeners
- ❌ Clicking buttons did nothing (no errors, no actions)

---

## Fixes Applied

### 1. Added window.NOVA_CONFIG_FORM Object ✅

**File:** `templates/config_form.html` (after line 613)

```html
<script>
    // --- Configuration data for external config_form.js ---
    window.NOVA_CONFIG_FORM = {
        currentUsername: "{{ 'default' if SINGLE_USER_MODE else ... }}",
        telemetryEnabled: {{ 'true' if telemetry_enabled else 'false' }},
        urls: {
            updateComponent: "{{ url_for('tools.update_component') }}",
            deleteComponent: "{{ url_for('tools.delete_component') }}",
            deleteRig: "{{ url_for('tools.delete_rig') }}",
            streamFetchDetails: "{{ url_for('tools.stream_fetch_details') }}",
            uploadEditorImage: "{{ url_for('tools.upload_editor_image') }}"
        }
    };
```

**Why needed:** config_form.js expects this object to exist

### 2. Added Script Tag to Load config_form.js ✅

**File:** `templates/config_form.html` (after line 1550)

```html
</script>

<script src="{{ url_for('static', filename='js/config_form.js') }}" defer></script>

{% endblock %}
```

**Effect:** config_form.js now loads and event delegation runs!

---

## What Happens Now

**Load Order:**
1. Template's embedded `<script>` runs (old code)
2. DOM finishes loading
3. `config_form.js` runs (deferred, with new event delegation)
4. config_form.js defines event listeners:
   ```javascript
   document.addEventListener('click', function(e) {
       const target = e.target.closest('[data-action]');
       if (!target) return;

       const action = target.dataset.action;
       console.log('[CONFIG_FORM] Click action triggered:', action, target);

       switch(action) {
           case 'edit-rig': ...
           case 'import-shared': ...
           case 'show-shared-notes': ...
           case 'trigger-file-input': ...
           // ...
       }
   });
   ```

**Result:** Buttons now work!

---

## Testing Instructions

### 1. Hard Refresh Browser
```bash
Cmd+Shift+R (Mac) / Ctrl+F5 (Windows/Linux)
```

**CRITICAL:** Hard refresh is required to load the new script tag!

### 2. Open DevTools Console

### 3. Verify Script Loads

**Check Network tab:**
- Look for `config_form.js` in network requests
- Status should be `200 OK`
- ✅ If present: Script is loading

**Check Console on page load:**
- Should see no errors related to `NOVA_CONFIG_FORM`
- ✅ If clean: Configuration object is valid

### 4. Test All Buttons

#### Import .hzn Button:
1. Click "Import .hzn" button
2. **Expected console:**
   ```
   [CONFIG_FORM] Click action triggered: trigger-file-input <button>
   [CONFIG_FORM] trigger-file-input: hzn_import_new
   ```
3. **Expected behavior:** File picker opens

#### Edit Rig Button:
1. Click "Edit" on any rig
2. **Expected console:**
   ```
   [CONFIG_FORM] Click action triggered: edit-rig <button>
   [CONFIG_FORM] edit-rig: <rig_id>
   ```
3. **Expected behavior:** Form populates with rig data

#### View Shared Notes:
1. Click "View" in shared objects notes column
2. **Expected console:**
   ```
   [CONFIG_FORM] Click action triggered: show-shared-notes <button>
   [CONFIG_FORM] show-shared-notes: <object_name>
   ```
3. **Expected behavior:** Modal opens with notes

#### Close Modal:
1. Click "Close" button in modal OR click backdrop
2. **Expected console:**
   ```
   [CONFIG_FORM] Click action triggered: close-notes-modal <div>
   [CONFIG_FORM] close-notes-modal
   ```
3. **Expected behavior:** Modal closes

#### Import Shared Item:
1. Click "Import" on shared object/component/view
2. **Expected console:**
   ```
   [CONFIG_FORM] Click action triggered: import-shared <button>
   [CONFIG_FORM] import-shared: <id> object
   ```
3. **Expected behavior:** Item imports, button changes to "Imported"

---

## If Buttons Still Don't Work

### Check 1: Script Loading
Open DevTools Console and run:
```javascript
console.log('config_form.js loaded:', typeof populateComponentFormForEdit);
console.log('NOVA_CONFIG_FORM:', window.NOVA_CONFIG_FORM);
```

**Expected output:**
```
config_form.js loaded: function
NOVA_CONFIG_FORM: {currentUsername: '...', urls: {...}, telemetryEnabled: true}
```

**If undefined:** Script didn't load - check Network tab

### Check 2: Event Listeners
```javascript
// Check if click listener is attached
console.log('Event listeners on document:', getEventListeners(document));
```

**Expected:** Should see `click` event listener

### Check 3: Button Data Attributes
```javascript
// Click any button and run this in console
console.log('Button attributes:', $0.dataset);
```

**Expected:** Should see `action: "edit-rig"` or similar

---

## Files Modified

### templates/config_form.html
- ✅ Added `window.NOVA_CONFIG_FORM` object (after line 613)
- ✅ Added script tag to load config_form.js (after line 1550)

---

## Summary

### What Was Wrong ❌
- config_form.html had NO script tag for config_form.js
- Event delegation code existed but never ran
- Buttons had data-action attributes but no listeners
- Result: Clicks did nothing, no errors

### What's Fixed Now ✅
- ✅ window.NOVA_CONFIG_FORM object created with all required data
- ✅ Script tag added to load config_form.js with defer
- ✅ Event delegation now runs when DOM loads
- ✅ Console logging added for debugging
- ✅ All buttons should work now

---

## Next Steps

1. **Hard refresh** (Cmd+Shift+R / Ctrl+F5) - CRITICAL!
2. **Open DevTools Console**
3. **Test each button type** (edit, import, view, trigger-file-input)
4. **Watch for console logs** starting with `[CONFIG_FORM]`
5. **Report results:**
   - Which buttons work?
   - Do you see console logs?
   - Any errors?

---

**Emergency Fix #5 Complete** ✅

**Authored by:** Claude Sonnet 4.5
**Project:** Nova DSO Tracker
**This was the critical missing piece!**
