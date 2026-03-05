/**
 * English translations for Nova DSO Tracker
 *
 * This file contains all user-facing strings from JavaScript files.
 * Keys should match the string used in window.t('key') calls.
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.en = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "Loading...",
    "calculating": "Calculating...",
    "error": "Error",
    "success": "Success",
    "cancel": "Cancel",
    "save": "Save",
    "delete": "Delete",
    "edit": "Edit",
    "close": "Close",
    "confirm": "Confirm",
    "yes": "Yes",
    "no": "No",
    "name": "Name",
    "description": "Description",
    "notes": "Notes",
    "date": "Date",
    "time": "Time",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "Dashboard",
    "objects": "Objects",
    "journal": "Journal",
    "heatmap": "Heatmap",
    "outlook": "Outlook",
    "inspiration": "Inspiration",

    // Saved Views
    "saved_views": "Saved Views",
    "saved_views_placeholder": "-- Saved Views --",
    "error_loading_views": "Error loading views",
    "name_required": "Name is required",
    "error_saving_view": "Error saving view: {error}",
    "error_deleting_view": "Error deleting view: {error}",
    "confirm_delete_view": "Are you sure you want to delete the view \"{name}\"?",
    "error_load_view_data": "Error: Could not load view data from cache.",

    // Simulation Mode
    "simulation": "Simulation",
    "simulation_mode": "Simulation Mode",
    "simulated": "Simulated",
    "mode": "Mode",
    "update": "Update",

    // Data Loading
    "data_load_failed": "Data Load Failed: {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "Object",
    "common_name": "Common Name",
    "constellation": "Constellation",
    "type": "Type",
    "magnitude": "Magnitude",
    "altitude": "Altitude",
    "azimuth": "Azimuth",
    "transit_time": "Transit Time",
    "observable_duration": "Observable Duration",
    "max_altitude": "Max Altitude",
    "moon_separation": "Moon Separation",
    "trend": "Trend",
    "sb": "SB",
    "size": "Size",
    "best_month": "Best Month",
    "current": "Current",
    "local_time": "Local Time",
    "minutes": "minutes",

    // Status Strip
    "location": "Location",
    "moon": "Moon",
    "dusk": "Dusk",
    "dawn": "Dawn",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "sep",
    "failed_update_active_project": "Failed to update Active Project: {error}",
    "successfully_updated_active_project": "Successfully updated Active Project status for {object} to {status}",
    "simbad_requires_internet": "SIMBAD requires an active internet connection to load data.",
    "simbad_requires_internet_short": "SIMBAD requires an active internet connection.",
    "no_imaging_opportunities": "No good imaging opportunities found within your search criteria.",
    "error_loading_opportunities": "Error loading opportunities: {error}",
    "failed_load_opportunities": "Failed to load imaging opportunities. Check console for details. ({error})",
    "add_to_calendar": "Add to calendar",
    "view_inspiration": "View Inspiration",
    "add_own_inspiration": "Add Your Own Inspiration!",
    "seeing_automated_survey_images": "You are currently seeing automated survey images (DSS2).",
    "display_own_astrophotos": "Did you know you can display your own astrophotos or favorite reference images here?",
    "go_to_config_manage_objects": "Go to <strong>Configuration &gt; Manage Objects</strong> to add \"Inspiration Content\" (Image URLs, Credits, Descriptions) to your targets.",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "Showing {count} objects",
    "showing_objects_of": "Showing {visible} of {total} objects",
    "no_objects_selected": "No objects selected.",
    "confirm_bulk_action": "Are you sure you want to {action} {count} objects?",
    "bulk_action_failed": "Bulk action failed. See console.",
    "bulk_fetch_details_failed": "Bulk fetch details failed. See console.",
    "fetching_details_for": "Fetching details for {count} objects...",
    "no_potential_duplicates": "No potential duplicates found based on coordinates.",
    "all_duplicates_resolved": "All duplicates resolved!",
    "error_scanning_duplicates": "Error scanning for duplicates.",
    "merge_confirm": "Merge '{merge}' INTO '{keep}'?\n\nThis will:\n1. Re-link journals/projects from {merge} to {keep}\n2. Copy notes from {merge}\n3. DELETE {merge} permanently",
    "keep_a_merge_b": "Keep A, Merge B",
    "keep_b_merge_a": "Keep B, Merge A",
    "no_telescopes": "No telescopes defined.",
    "no_cameras": "No cameras defined.",
    "no_reducers": "No reducers defined.",
    "no_rigs": "No rigs configured yet.",
    "selected": "Selected",
    "please_enter_object_id": "Please enter an object identifier.",
    "checking_local_library": "Checking your local library for {name}...",
    "object_found_library": "Object '{name}' found in your library. Loading for edit.",
    "object_not_found_simbad": "Object not found in local library. Checking SIMBAD...",
    "found_details_loaded": "Found: {name}. Details loaded from SIMBAD.",
    "error_fetching_simbad": "Error: {error}.\nYou can now add the object manually and click 'Confirm Add'.",
    "warning_ra_degrees": "Warning: RA value ({ra}) is > 24, which implies Degrees.\n\nDo you want to automatically convert this to {corrected} Hours?",
    "importing_catalog": "Importing '{name}'...\n\nThis will update your library with data from the server:\n• New objects from this pack will be added.\n• Existing objects will be updated with the latest images/descriptions.\n• Your personal Project Notes, Status, and Framings remain safe.\n\nDo you want to proceed?",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "Update Component",
    "update_rig": "Update Rig",
    "confirm_delete_component": "Deleting a component is permanent and cannot be undone. Are you sure?",
    "confirm_delete_rig": "Are you sure you want to delete the rig '{name}'?",
    "select_telescope": "-- Select a Telescope --",
    "select_camera": "-- Select a Camera --",
    "none": "-- None --",
    "telescope": "Telescope",
    "camera": "Camera",
    "reducer_extender": "Reducer/Extender",
    "guiding": "Guiding",
    "owner": "Owner",
    "import": "Import",
    "imported": "Imported",
    "importing": "Importing...",
    "confirm_import_item": "Are you sure you want to import this {type}?",
    "import_failed": "Import failed. See console for details.",
    "no_shared_objects": "No shared objects found from other users.",
    "no_shared_components": "No shared components found from other users.",
    "no_shared_views": "No shared views found from other users.",
    "error_loading_shared": "Error loading shared items.",
    "view": "View",
    "saving": "Saving...",
    "saved": "Saved!",
    "error_saving": "Error saving: {error}",
    "network_error_saving": "Network error saving object.",
    "connecting": "Connecting...",
    "importing_please_wait": "Importing {entity}, please wait...",
    "import_failed_server": "Import failed: Server returned status {status}",
    "upload_error": "Upload error: {error}",
    "upload_failed": "Upload failed. See console.",
    "done": "Done",
    "shared_notes_for": "Shared Notes for {name}",
    "confirm_fetch_details": "This will scan all your objects and fetch missing details (Type, Magnitude, Size, etc.) from external databases.\n\nDepending on your library size, this may take a few moments.\n\nProceed?",
    "connection_lost_refreshing": "Connection lost. Refreshing page...",
    "error_preparing_print": "Error preparing print view: {error}",

    // Sampling
    "oversampled": "Oversampled",
    "slightly_oversampled": "Slightly Oversampled",
    "good_sampling": "Good Sampling",
    "slightly_undersampled": "Slightly Undersampled",
    "undersampled": "Undersampled",
    "px_fwhm": "px/FWHM",
    "tip_binning": "Tip: 2x2 binning would yield ~{scale}\"/px ({sampling} px/FWHM)",
    "check_software_max": " — check your software's max",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "Report frame not found.",
    "preparing_print_view": "Preparing Print View...",
    "merging_session": "Merging Session {current}/{total}...",
    "check_popup": "Check Popup...",
    "appendix_session": "APPENDIX: SESSION {number}",
    "add_new_session": "Add New Session",
    "add_session": "Add Session",
    "save_changes": "Save Changes",
    "editing_session": "Editing Session: {date}",
    "visibility": "Visibility: {date}",
    "altitude_deg": "Altitude (°)",
    "recommendation": "Recommendation: {pixels} px",
    "time_limit_reached": "(Time Limit Reached)",
    "max_real_subs": "(Max {max} | Real {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "Loading help content...",
    "help_content_empty": "Error: Help content returned empty.",
    "network_error_help": "Network Error: Could not load help topic '{topic}'.",
    "latest_version": "Latest version: v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "Saved successfully",
    "deleted_successfully": "Deleted successfully",
    "confirm_delete": "Are you sure you want to delete this?",
    "unsaved_changes": "You have unsaved changes. Are you sure you want to leave?",
    "network_error": "Network error. Please try again.",
    "session_expired": "Your session has expired. Please log in again.",
    "error_with_message": "Error: {message}",
    "failed_with_error": "Failed: {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "Altitude Chart",
    "altitude_degrees": "Altitude (°)",
    "time_hours": "Time (hours)",
    "tonight": "Tonight",
    "show_framing": "Show Framing",
    "hide_framing": "Hide Framing",
    "horizon": "Horizon",
    "moon_altitude": "Moon Altitude",
    "object_altitude": "{object} Altitude",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "Help",
    "about": "About",
    "about_nova_dso_tracker": "About Nova DSO Tracker",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "Select language",
    "toggle_theme": "Toggle theme",
    "toggle_dark_light_theme": "Toggle dark/light theme"
};
