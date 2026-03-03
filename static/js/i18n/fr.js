/**
 * French translations for Nova DSO Tracker
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.fr = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "Chargement...",
    "calculating": "Calcul...",
    "error": "Erreur",
    "success": "Succès",
    "cancel": "Annuler",
    "save": "Enregistrer",
    "delete": "Supprimer",
    "edit": "Modifier",
    "close": "Fermer",
    "confirm": "Confirmer",
    "yes": "Oui",
    "no": "Non",
    "name": "Nom",
    "description": "Description",
    "notes": "Notes",
    "date": "Date",
    "time": "Heure",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "Tableau de bord",
    "objects": "Objets",
    "journal": "Journal",
    "heatmap": "Carte de chaleur",
    "outlook": "Perspectives",
    "inspiration": "Inspiration",

    // Saved Views
    "saved_views": "Vues enregistrées",
    "saved_views_placeholder": "-- Vues enregistrées --",
    "error_loading_views": "Erreur lors du chargement des vues",
    "name_required": "Le nom est requis",
    "error_saving_view": "Erreur lors de l'enregistrement de la vue : {error}",
    "error_deleting_view": "Erreur lors de la suppression de la vue : {error}",
    "confirm_delete_view": "Êtes-vous sûr de vouloir supprimer la vue \"{name}\" ?",
    "error_load_view_data": "Erreur : Impossible de charger les données de vue depuis le cache.",

    // Simulation Mode
    "simulation": "Simulation",
    "simulation_mode": "Mode Simulation",
    "simulated": "Simulé",
    "mode": "Mode",
    "update": "Mise à jour",

    // Data Loading
    "data_load_failed": "Échec du chargement des données : {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "Objet",
    "common_name": "Nom commun",
    "constellation": "Constellation",
    "type": "Type",
    "magnitude": "Magnitude",
    "altitude": "Altitude",
    "azimuth": "Azimut",
    "transit_time": "Heure de transit",
    "observable_duration": "Durée observable",
    "max_altitude": "Altitude max",
    "moon_separation": "Séparation lunaire",
    "trend": "Tendance",
    "sb": "SB",
    "size": "Taille",
    "best_month": "Meilleur mois",
    "current": "Actuel",
    "local_time": "Heure locale",
    "minutes": "minutes",

    // Status Strip
    "location": "Emplacement",
    "moon": "Lune",
    "dusk": "Crépuscule",
    "dawn": "Aube",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "sép",
    "failed_update_active_project": "Échec de la mise à jour du projet actif : {error}",
    "successfully_updated_active_project": "Statut du projet actif pour {object} mis à jour avec succès vers {status}",
    "simbad_requires_internet": "SIMBAD nécessite une connexion Internet active pour charger les données.",
    "simbad_requires_internet_short": "SIMBAD nécessite une connexion Internet active.",
    "no_imaging_opportunities": "Aucune bonne opportunité d'imagerie trouvée dans vos critères de recherche.",
    "error_loading_opportunities": "Erreur lors du chargement des opportunités : {error}",
    "failed_load_opportunities": "Échec du chargement des opportunités d'imagerie. Voir la console pour plus de détails. ({error})",
    "add_to_calendar": "Ajouter au calendrier",
    "view_inspiration": "Voir l'inspiration",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "Affichage de {count} objets",
    "showing_objects_of": "Affichage de {visible} sur {total} objets",
    "no_objects_selected": "Aucun objet sélectionné.",
    "confirm_bulk_action": "Êtes-vous sûr de vouloir {action} {count} objets ?",
    "bulk_action_failed": "L'action groupée a échoué. Voir la console.",
    "bulk_fetch_details_failed": "La récupération groupée des détails a échoué. Voir la console.",
    "fetching_details_for": "Récupération des détails pour {count} objets...",
    "no_potential_duplicates": "Aucun doublon potentiel trouvé basé sur les coordonnées.",
    "all_duplicates_resolved": "Tous les doublons ont été résolus !",
    "error_scanning_duplicates": "Erreur lors de la recherche de doublons.",
    "merge_confirm": "Fusionner '{merge}' DANS '{keep}' ?\n\nCela va :\n1. Relier les journaux/projets de {merge} vers {keep}\n2. Copier les notes de {merge}\n3. SUPPRIMER {merge} définitivement",
    "keep_a_merge_b": "Garder A, Fusionner B",
    "keep_b_merge_a": "Garder B, Fusionner A",
    "no_telescopes": "Aucun télescope défini.",
    "no_cameras": "Aucune caméra définie.",
    "no_reducers": "Aucun réducteur défini.",
    "no_rigs": "Aucune configuration définie.",
    "selected": "Sélectionné",
    "please_enter_object_id": "Veuillez entrer un identifiant d'objet.",
    "checking_local_library": "Vérification de votre bibliothèque locale pour {name}...",
    "object_found_library": "Objet '{name}' trouvé dans votre bibliothèque. Chargement pour modification.",
    "object_not_found_simbad": "Objet non trouvé dans la bibliothèque locale. Vérification SIMBAD...",
    "found_details_loaded": "Trouvé : {name}. Détails chargés depuis SIMBAD.",
    "error_fetching_simbad": "Erreur : {error}.\nVous pouvez maintenant ajouter l'objet manuellement et cliquer sur 'Confirmer'.",
    "warning_ra_degrees": "Attention : La valeur RA ({ra}) est > 24, ce qui implique des degrés.\n\nVoulez-vous convertir automatiquement en {corrected} heures ?",
    "importing_catalog": "Importation de '{name}'...\n\nCela mettra à jour votre bibliothèque avec les données du serveur :\n• Les nouveaux objets de ce pack seront ajoutés.\n• Les objets existants seront mis à jour avec les dernières images/descriptions.\n• Vos notes de projet personnelles, statut et cadrages restent sécurisés.\n\nVoulez-vous continuer ?",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "Mettre à jour le composant",
    "update_rig": "Mettre à jour la configuration",
    "confirm_delete_component": "La suppression d'un composant est permanente et ne peut pas être annulée. Êtes-vous sûr ?",
    "confirm_delete_rig": "Êtes-vous sûr de vouloir supprimer la configuration '{name}' ?",
    "select_telescope": "-- Sélectionner un télescope --",
    "select_camera": "-- Sélectionner une caméra --",
    "none": "-- Aucun --",
    "telescope": "Télescope",
    "camera": "Caméra",
    "reducer_extender": "Réducteur/Extender",
    "guiding": "Guidage",
    "owner": "Propriétaire",
    "import": "Importer",
    "imported": "Importé",
    "importing": "Importation...",
    "confirm_import_item": "Êtes-vous sûr de vouloir importer ce {type} ?",
    "import_failed": "L'importation a échoué. Voir la console pour plus de détails.",
    "no_shared_objects": "Aucun objet partagé trouvé par d'autres utilisateurs.",
    "no_shared_components": "Aucun composant partagé trouvé par d'autres utilisateurs.",
    "no_shared_views": "Aucune vue partagée trouvée par d'autres utilisateurs.",
    "error_loading_shared": "Erreur lors du chargement des éléments partagés.",
    "view": "Voir",
    "saving": "Enregistrement...",
    "saved": "Enregistré !",
    "error_saving": "Erreur lors de l'enregistrement : {error}",
    "network_error_saving": "Erreur réseau lors de l'enregistrement de l'objet.",
    "connecting": "Connexion...",
    "importing_please_wait": "Importation de {entity}, veuillez patienter...",
    "import_failed_server": "L'importation a échoué : Le serveur a retourné le statut {status}",
    "upload_error": "Erreur de téléchargement : {error}",
    "upload_failed": "Le téléchargement a échoué. Voir la console.",
    "done": "Terminé",
    "shared_notes_for": "Notes partagées pour {name}",
    "confirm_fetch_details": "Cela analysera tous vos objets et récupérera les détails manquants (Type, Magnitude, Taille, etc.) auprès de bases de données externes.\n\nSelon la taille de votre bibliothèque, cela peut prendre quelques instants.\n\nContinuer ?",
    "connection_lost_refreshing": "Connexion perdue. Actualisation de la page...",
    "error_preparing_print": "Erreur lors de la préparation de l'aperçu d'impression : {error}",

    // Sampling
    "oversampled": "Suréchantillonné",
    "slightly_oversampled": "Légèrement suréchantillonné",
    "good_sampling": "Bon échantillonnage",
    "slightly_undersampled": "Légèrement sous-échantillonné",
    "undersampled": "Sous-échantillonné",
    "px_fwhm": "px/FWHM",
    "tip_binning": "Astuce : le binning 2x2 donnerait ~{scale}\"/px ({sampling} px/FWHM)",
    "check_software_max": " — vérifiez le maximum de votre logiciel",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "Cadre de rapport non trouvé.",
    "preparing_print_view": "Préparation de l'aperçu d'impression...",
    "merging_session": "Fusion de la session {current}/{total}...",
    "check_popup": "Vérifier le popup...",
    "appendix_session": "ANNEXE : SESSION {number}",
    "add_new_session": "Ajouter une nouvelle session",
    "add_session": "Ajouter une session",
    "save_changes": "Enregistrer les modifications",
    "editing_session": "Modification de la session : {date}",
    "visibility": "Visibilité : {date}",
    "altitude_deg": "Altitude (°)",
    "recommendation": "Recommandation : {pixels} px",
    "time_limit_reached": "(Limite de temps atteinte)",
    "max_real_subs": "(Max {max} | Réel {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "Chargement du contenu d'aide...",
    "help_content_empty": "Erreur : Le contenu d'aide retourné est vide.",
    "network_error_help": "Erreur réseau : Impossible de charger le sujet d'aide '{topic}'.",
    "latest_version": "Dernière version : v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "Enregistré avec succès",
    "deleted_successfully": "Supprimé avec succès",
    "confirm_delete": "Êtes-vous sûr de vouloir supprimer ceci ?",
    "unsaved_changes": "Vous avez des modifications non enregistrées. Êtes-vous sûr de vouloir quitter ?",
    "network_error": "Erreur réseau. Veuillez réessayer.",
    "session_expired": "Votre session a expiré. Veuillez vous reconnecter.",
    "error_with_message": "Erreur : {message}",
    "failed_with_error": "Échec : {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "Graphique d'altitude",
    "altitude_degrees": "Altitude (°)",
    "time_hours": "Temps (heures)",
    "tonight": "Cette nuit",
    "show_framing": "Afficher le cadrage",
    "hide_framing": "Masquer le cadrage",
    "horizon": "Horizon",
    "moon_altitude": "Altitude lunaire",
    "object_altitude": "Altitude de {object}",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "Aide",
    "about": "À propos",
    "about_nova_dso_tracker": "À propos de Nova DSO Tracker",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "Sélectionner la langue",
    "toggle_theme": "Changer le thème",
    "toggle_dark_light_theme": "Basculer le thème sombre/clair"
};
