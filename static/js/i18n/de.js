/**
 * German translations for Nova DSO Tracker
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.de = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "Laden...",
    "calculating": "Berechnen...",
    "error": "Fehler",
    "success": "Erfolg",
    "cancel": "Abbrechen",
    "save": "Speichern",
    "delete": "Löschen",
    "edit": "Bearbeiten",
    "close": "Schließen",
    "confirm": "Bestätigen",
    "yes": "Ja",
    "no": "Nein",
    "name": "Name",
    "description": "Beschreibung",
    "notes": "Notizen",
    "date": "Datum",
    "time": "Zeit",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "Dashboard",
    "objects": "Objekte",
    "journal": "Journal",
    "heatmap": "Heatmap",
    "outlook": "Ausblick",
    "inspiration": "Inspiration",

    // Saved Views
    "saved_views": "Gespeicherte Ansichten",
    "saved_views_placeholder": "-- Gespeicherte Ansichten --",
    "error_loading_views": "Fehler beim Laden der Ansichten",
    "name_required": "Name ist erforderlich",
    "error_saving_view": "Fehler beim Speichern der Ansicht: {error}",
    "error_deleting_view": "Fehler beim Löschen der Ansicht: {error}",
    "confirm_delete_view": "Sind Sie sicher, dass Sie die Ansicht \"{name}\" löschen möchten?",
    "error_load_view_data": "Fehler: Ansichtsdaten konnten nicht aus dem Cache geladen werden.",

    // Simulation Mode
    "simulation": "Simulation",
    "simulation_mode": "Simulationsmodus",
    "simulated": "Simuliert",
    "mode": "Modus",
    "update": "Aktualisieren",

    // Data Loading
    "data_load_failed": "Datenladung fehlgeschlagen: {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "Objekt",
    "common_name": "Allgemeiner Name",
    "constellation": "Sternbild",
    "type": "Typ",
    "magnitude": "Magnitude",
    "altitude": "Höhe",
    "azimuth": "Azimut",
    "transit_time": "Kulminationszeit",
    "observable_duration": "Beobachtbare Dauer",
    "max_altitude": "Max. Höhe",
    "moon_separation": "Mondabstand",
    "trend": "Trend",
    "sb": "SF",
    "size": "Größe",
    "best_month": "Bester Monat",
    "current": "Aktuell",
    "local_time": "Ortszeit",
    "minutes": "Minuten",

    // Status Strip
    "location": "Standort",
    "moon": "Mond",
    "dusk": "Dämmerung",
    "dawn": "Morgengrauen",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "sep",
    "failed_update_active_project": "Fehler beim Aktualisieren des aktiven Projekts: {error}",
    "successfully_updated_active_project": "Aktiver Projektstatus für {object} erfolgreich auf {status} aktualisiert",
    "simbad_requires_internet": "SIMBAD erfordert eine aktive Internetverbindung zum Laden von Daten.",
    "simbad_requires_internet_short": "SIMBAD erfordert eine aktive Internetverbindung.",
    "no_imaging_opportunities": "Keine guten Imaging-Möglichkeiten innerhalb Ihrer Suchkriterien gefunden.",
    "error_loading_opportunities": "Fehler beim Laden der Möglichkeiten: {error}",
    "failed_load_opportunities": "Fehler beim Laden der Imaging-Möglichkeiten. Siehe Konsole für Details. ({error})",
    "add_to_calendar": "Zum Kalender hinzufügen",
    "view_inspiration": "Inspiration anzeigen",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "Zeige {count} Objekte",
    "showing_objects_of": "Zeige {visible} von {total} Objekten",
    "no_objects_selected": "Keine Objekte ausgewählt.",
    "confirm_bulk_action": "Sind Sie sicher, dass Sie {action} für {count} Objekte durchführen möchten?",
    "bulk_action_failed": "Massenaktion fehlgeschlagen. Siehe Konsole.",
    "bulk_fetch_details_failed": "Massenabruf von Details fehlgeschlagen. Siehe Konsole.",
    "fetching_details_for": "Details für {count} Objekte abrufen...",
    "no_potential_duplicates": "Keine potenziellen Duplikate basierend auf Koordinaten gefunden.",
    "all_duplicates_resolved": "Alle Duplikate behoben!",
    "error_scanning_duplicates": "Fehler beim Scannen nach Duplikaten.",
    "merge_confirm": "'{merge}' IN '{keep}' zusammenführen?\n\nDies wird:\n1. Journale/Projekte von {merge} zu {keep} neu verknüpfen\n2. Notizen von {merge} kopieren\n3. {merge} dauerhaft LÖSCHEN",
    "keep_a_merge_b": "A behalten, B zusammenführen",
    "keep_b_merge_a": "B behalten, A zusammenführen",
    "no_telescopes": "Keine Teleskope definiert.",
    "no_cameras": "Keine Kameras definiert.",
    "no_reducers": "Keine Reduzierer definiert.",
    "no_rigs": "Noch keine Rigs konfiguriert.",
    "selected": "Ausgewählt",
    "please_enter_object_id": "Bitte geben Sie eine Objektkennung ein.",
    "checking_local_library": "Überprüfe Ihre lokale Bibliothek auf {name}...",
    "object_found_library": "Objekt '{name}' in Ihrer Bibliothek gefunden. Laden zum Bearbeiten.",
    "object_not_found_simbad": "Objekt nicht in lokaler Bibliothek gefunden. Überprüfe SIMBAD...",
    "found_details_loaded": "Gefunden: {name}. Details von SIMBAD geladen.",
    "error_fetching_simbad": "Fehler: {error}.\nSie können das Objekt jetzt manuell hinzufügen und auf 'Bestätigen' klicken.",
    "warning_ra_degrees": "Warnung: RA-Wert ({ra}) ist > 24, was Grad bedeutet.\n\nMöchten Sie dies automatisch in {corrected} Stunden umrechnen?",
    "importing_catalog": "Importiere '{name}'...\n\nDies wird Ihre Bibliothek mit Daten vom Server aktualisieren:\n• Neue Objekte aus diesem Paket werden hinzugefügt.\n• Bestehende Objekte werden mit den neuesten Bildern/Beschreibungen aktualisiert.\n• Ihre persönlichen Projektnotizen, Status und Framings bleiben erhalten.\n\nMöchten Sie fortfahren?",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "Komponente aktualisieren",
    "update_rig": "Rig aktualisieren",
    "confirm_delete_component": "Das Löschen einer Komponente ist dauerhaft und kann nicht rückgängig gemacht werden. Sind Sie sicher?",
    "confirm_delete_rig": "Sind Sie sicher, dass Sie das Rig '{name}' löschen möchten?",
    "select_telescope": "-- Wählen Sie ein Teleskop --",
    "select_camera": "-- Wählen Sie eine Kamera --",
    "none": "-- Keine --",
    "telescope": "Teleskop",
    "camera": "Kamera",
    "reducer_extender": "Reduzierer/Extender",
    "guiding": "Nachführung",
    "owner": "Eigentümer",
    "import": "Importieren",
    "imported": "Importiert",
    "importing": "Importiere...",
    "confirm_import_item": "Sind Sie sicher, dass Sie diesen {type} importieren möchten?",
    "import_failed": "Import fehlgeschlagen. Siehe Konsole für Details.",
    "no_shared_objects": "Keine geteilten Objekte von anderen Benutzern gefunden.",
    "no_shared_components": "Keine geteilten Komponenten von anderen Benutzern gefunden.",
    "no_shared_views": "Keine geteilten Ansichten von anderen Benutzern gefunden.",
    "error_loading_shared": "Fehler beim Laden geteilter Elemente.",
    "view": "Anzeigen",
    "saving": "Speichere...",
    "saved": "Gespeichert!",
    "error_saving": "Fehler beim Speichern: {error}",
    "network_error_saving": "Netzwerkfehler beim Speichern des Objekts.",
    "connecting": "Verbinde...",
    "importing_please_wait": "Importiere {entity}, bitte warten...",
    "import_failed_server": "Import fehlgeschlagen: Server gab Status {status} zurück",
    "upload_error": "Upload-Fehler: {error}",
    "upload_failed": "Upload fehlgeschlagen. Siehe Konsole.",
    "done": "Fertig",
    "shared_notes_for": "Geteilte Notizen für {name}",
    "confirm_fetch_details": "Dies wird alle Ihre Objekte scannen und fehlende Details (Typ, Magnitude, Größe usw.) von externen Datenbanken abrufen.\n\nJe nach Größe Ihrer Bibliothek kann dies einige Momente dauern.\n\nFortfahren?",
    "connection_lost_refreshing": "Verbindung verloren. Seite wird aktualisiert...",
    "error_preparing_print": "Fehler beim Vorbereiten der Druckansicht: {error}",

    // Sampling
    "oversampled": "Oversampled",
    "slightly_oversampled": "Leicht Oversampled",
    "good_sampling": "Gutes Sampling",
    "slightly_undersampled": "Leicht Undersampled",
    "undersampled": "Undersampled",
    "px_fwhm": "px/FWHM",
    "tip_binning": "Tipp: 2x2 Binning würde ~{scale}\"/px ergeben ({sampling} px/FWHM)",
    "check_software_max": " — überprüfen Sie Ihr Software-Maximum",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "Berichtsframe nicht gefunden.",
    "preparing_print_view": "Druckansicht wird vorbereitet...",
    "merging_session": "Zusammenführen von Sitzung {current}/{total}...",
    "check_popup": "Popup überprüfen...",
    "appendix_session": "ANHANG: SITZUNG {number}",
    "add_new_session": "Neue Sitzung hinzufügen",
    "add_session": "Sitzung hinzufügen",
    "save_changes": "Änderungen speichern",
    "editing_session": "Bearbeite Sitzung: {date}",
    "visibility": "Sichtbarkeit: {date}",
    "altitude_deg": "Höhe (°)",
    "recommendation": "Empfehlung: {pixels} px",
    "time_limit_reached": "(Zeitlimit erreicht)",
    "max_real_subs": "(Max {max} | Real {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "Hilfeinhalt wird geladen...",
    "help_content_empty": "Fehler: Hilfeinhalt leer zurückgegeben.",
    "network_error_help": "Netzwerkfehler: Hilfethema '{topic}' konnte nicht geladen werden.",
    "latest_version": "Neueste Version: v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "Erfolgreich gespeichert",
    "deleted_successfully": "Erfolgreich gelöscht",
    "confirm_delete": "Sind Sie sicher, dass Sie dies löschen möchten?",
    "unsaved_changes": "Sie haben ungespeicherte Änderungen. Sind Sie sicher, dass Sie die Seite verlassen möchten?",
    "network_error": "Netzwerkfehler. Bitte versuchen Sie es erneut.",
    "session_expired": "Ihre Sitzung ist abgelaufen. Bitte melden Sie sich erneut an.",
    "error_with_message": "Fehler: {message}",
    "failed_with_error": "Fehlgeschlagen: {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "Höhenchart",
    "altitude_degrees": "Höhe (°)",
    "time_hours": "Zeit (Stunden)",
    "tonight": "Heute Nacht",
    "show_framing": "Bildausschnitt anzeigen",
    "hide_framing": "Bildausschnitt ausblenden",
    "horizon": "Horizont",
    "moon_altitude": "Mondhöhe",
    "object_altitude": "{object} Höhe",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "Hilfe",
    "about": "Über",
    "about_nova_dso_tracker": "Über Nova DSO Tracker",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "Sprache auswählen",
    "toggle_theme": "Thema umschalten",
    "toggle_dark_light_theme": "Dunkel/Hell-Thema umschalten"
};
