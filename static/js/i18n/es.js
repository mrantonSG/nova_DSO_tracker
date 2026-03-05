/**
 * Spanish translations for Nova DSO Tracker
 */
window.NOVA_I18N = window.NOVA_I18N || {};
window.NOVA_I18N.es = {
    // ========================================================================
    // COMMON / GENERAL
    // ========================================================================
    "loading": "Cargando...",
    "calculating": "Calculando...",
    "error": "Error",
    "success": "Éxito",
    "cancel": "Cancelar",
    "save": "Guardar",
    "delete": "Eliminar",
    "edit": "Editar",
    "close": "Cerrar",
    "confirm": "Confirmar",
    "yes": "Sí",
    "no": "No",
    "name": "Nombre",
    "description": "Descripción",
    "notes": "Notas",
    "date": "Fecha",
    "time": "Hora",
    "na": "N/A",

    // ========================================================================
    // DASHBOARD
    // ========================================================================
    "dashboard": "Panel",
    "objects": "Objetos",
    "journal": "Diario",
    "heatmap": "Mapa de calor",
    "outlook": "Perspectivas",
    "inspiration": "Inspiración",

    // Saved Views
    "saved_views": "Vistas guardadas",
    "saved_views_placeholder": "-- Vistas guardadas --",
    "error_loading_views": "Error al cargar las vistas",
    "name_required": "El nombre es obligatorio",
    "error_saving_view": "Error al guardar la vista: {error}",
    "error_deleting_view": "Error al eliminar la vista: {error}",
    "confirm_delete_view": "¿Está seguro de que desea eliminar la vista \"{name}\"?",
    "error_load_view_data": "Error: No se pudieron cargar los datos de la vista desde la caché.",

    // Simulation Mode
    "simulation": "Simulación",
    "simulation_mode": "Modo Simulación",
    "simulated": "Simulado",
    "mode": "Modo",
    "update": "Actualizar",

    // Data Loading
    "data_load_failed": "Error al cargar datos: {error}",

    // ========================================================================
    // OBJECT TABLE
    // ========================================================================
    "object": "Objeto",
    "common_name": "Nombre común",
    "constellation": "Constelación",
    "type": "Tipo",
    "magnitude": "Magnitud",
    "altitude": "Altitud",
    "azimuth": "Acimut",
    "transit_time": "Hora de tránsito",
    "observable_duration": "Duración observable",
    "max_altitude": "Altitud máx",
    "moon_separation": "Separación lunar",
    "trend": "Tendencia",
    "sb": "SB",
    "size": "Tamaño",
    "best_month": "Mejor mes",
    "current": "Actual",
    "local_time": "Hora local",
    "minutes": "minutos",

    // Status Strip
    "location": "Ubicación",
    "moon": "Luna",
    "dusk": "Crepúsculo",
    "dawn": "Amanecer",

    // ========================================================================
    // GRAPH VIEW / OBJECT DETAIL
    // ========================================================================
    "sep": "sep",
    "failed_update_active_project": "Error al actualizar el proyecto activo: {error}",
    "successfully_updated_active_project": "Estado del proyecto activo para {object} actualizado correctamente a {status}",
    "simbad_requires_internet": "SIMBAD requiere una conexión a Internet activa para cargar datos.",
    "simbad_requires_internet_short": "SIMBAD requiere una conexión a Internet activa.",
    "no_imaging_opportunities": "No se encontraron buenas oportunidades de imagen dentro de sus criterios de búsqueda.",
    "error_loading_opportunities": "Error al cargar oportunidades: {error}",
    "failed_load_opportunities": "Error al cargar oportunidades de imagen. Consulte la consola para más detalles. ({error})",
    "add_to_calendar": "Añadir al calendario",
    "view_inspiration": "Ver inspiración",
    "add_own_inspiration": "¡Añade tu propia inspiración!",
    "seeing_automated_survey_images": "Actualmente está viendo imágenes de encuestas automatizadas (DSS2).",
    "display_own_astrophotos": "¿Sabías que aquí puedes mostrar tus propias astrofotos o tus imágenes de referencia favoritas?",
    "go_to_config_manage_objects": "Vaya a <strong>Configuración &gt; Administrar objetos</strong> para añadir «Contenido de inspiración» (URL de imágenes, créditos, descripciones) a sus objetivos.",

    // ========================================================================
    // OBJECTS SECTION
    // ========================================================================
    "showing_objects": "Mostrando {count} objetos",
    "showing_objects_of": "Mostrando {visible} de {total} objetos",
    "no_objects_selected": "Ningún objeto seleccionado.",
    "confirm_bulk_action": "¿Está seguro de que desea {action} {count} objetos?",
    "bulk_action_failed": "La acción masiva falló. Consulte la consola.",
    "bulk_fetch_details_failed": "La obtención masiva de detalles falló. Consulte la consola.",
    "fetching_details_for": "Obteniendo detalles para {count} objetos...",
    "no_potential_duplicates": "No se encontraron duplicados potenciales basados en coordenadas.",
    "all_duplicates_resolved": "¡Todos los duplicados resueltos!",
    "error_scanning_duplicates": "Error al buscar duplicados.",
    "merge_confirm": "¿Fusionar '{merge}' EN '{keep}'?\n\nEsto:\n1. Reenlazará diarios/proyectos de {merge} a {keep}\n2. Copiará notas de {merge}\n3. ELIMINARÁ {merge} permanentemente",
    "keep_a_merge_b": "Mantener A, Fusionar B",
    "keep_b_merge_a": "Mantener B, Fusionar A",
    "no_telescopes": "No hay telescopios definidos.",
    "no_cameras": "No hay cámaras definidas.",
    "no_reducers": "No hay reductores definidos.",
    "no_rigs": "No hay configuraciones definidas.",
    "selected": "Seleccionado",
    "please_enter_object_id": "Por favor introduzca un identificador de objeto.",
    "checking_local_library": "Verificando su biblioteca local para {name}...",
    "object_found_library": "Objeto '{name}' encontrado en su biblioteca. Cargando para editar.",
    "object_not_found_simbad": "Objeto no encontrado en la biblioteca local. Verificando SIMBAD...",
    "found_details_loaded": "Encontrado: {name}. Detalles cargados desde SIMBAD.",
    "error_fetching_simbad": "Error: {error}.\nAhora puede añadir el objeto manualmente y hacer clic en 'Confirmar'.",
    "warning_ra_degrees": "Advertencia: El valor RA ({ra}) es > 24, lo que implica grados.\n\n¿Desea convertir automáticamente esto a {corrected} horas?",
    "importing_catalog": "Importando '{name}'...\n\nEsto actualizará su biblioteca con datos del servidor:\n• Se añadirán nuevos objetos de este paquete.\n• Los objetos existentes se actualizarán con las últimas imágenes/descripciones.\n• Sus notas de proyecto personales, estado y encuadres permanecen seguros.\n\n¿Desea continuar?",

    // ========================================================================
    // CONFIG FORM
    // ========================================================================
    "update_component": "Actualizar componente",
    "update_rig": "Actualizar configuración",
    "confirm_delete_component": "Eliminar un componente es permanente y no se puede deshacer. ¿Está seguro?",
    "confirm_delete_rig": "¿Está seguro de que desea eliminar la configuración '{name}'?",
    "select_telescope": "-- Seleccione un telescopio --",
    "select_camera": "-- Seleccione una cámara --",
    "none": "-- Ninguno --",
    "telescope": "Telescopio",
    "camera": "Cámara",
    "reducer_extender": "Reductor/Extensor",
    "guiding": "Guía",
    "owner": "Propietario",
    "import": "Importar",
    "imported": "Importado",
    "importing": "Importando...",
    "confirm_import_item": "¿Está seguro de que desea importar este {type}?",
    "import_failed": "La importación falló. Consulte la consola para más detalles.",
    "no_shared_objects": "No se encontraron objetos compartidos de otros usuarios.",
    "no_shared_components": "No se encontraron componentes compartidos de otros usuarios.",
    "no_shared_views": "No se encontraron vistas compartidas de otros usuarios.",
    "error_loading_shared": "Error al cargar elementos compartidos.",
    "view": "Ver",
    "saving": "Guardando...",
    "saved": "¡Guardado!",
    "error_saving": "Error al guardar: {error}",
    "network_error_saving": "Error de red al guardar el objeto.",
    "connecting": "Conectando...",
    "importing_please_wait": "Importando {entity}, por favor espere...",
    "import_failed_server": "La importación falló: El servidor devolvió el estado {status}",
    "upload_error": "Error de carga: {error}",
    "upload_failed": "La carga falló. Consulte la consola.",
    "done": "Hecho",
    "shared_notes_for": "Notas compartidas para {name}",
    "confirm_fetch_details": "Esto escaneará todos sus objetos y obtendrá los detalles faltantes (Tipo, Magnitud, Tamaño, etc.) de bases de datos externas.\n\nDependiendo del tamaño de su biblioteca, esto puede tomar algunos momentos.\n\n¿Continuar?",
    "connection_lost_refreshing": "Conexión perdida. Actualizando página...",
    "error_preparing_print": "Error al preparar la vista de impresión: {error}",

    // Sampling
    "oversampled": "Sobremuestreado",
    "slightly_oversampled": "Ligeramente sobremuestreado",
    "good_sampling": "Buen muestreo",
    "effective_fl": "Distancia focal efectiva",
    "image_scale": "Escala de imagen",
    "field_of_view": "Campo de visión",
    "guiding": "Guiado",
    "slightly_undersampled": "Ligeramente submuestreado",
    "undersampled": "Submuestreado",
    "px_fwhm": "px/FWHM",
    "tip_binning": "Consejo: el binning 2x2 daría ~{scale}\"/px ({sampling} px/FWHM)",
    "check_software_max": " — verifique el máximo de su software",

    // ========================================================================
    // JOURNAL SECTION
    // ========================================================================
    "report_frame_not_found": "Marco de informe no encontrado.",
    "preparing_print_view": "Preparando vista de impresión...",
    "merging_session": "Fusionando sesión {current}/{total}...",
    "check_popup": "Verificar popup...",
    "appendix_session": "APÉNDICE: SESIÓN {number}",
    "add_new_session": "Añadir nueva sesión",
    "add_session": "Añadir sesión",
    "save_changes": "Guardar cambios",
    "editing_session": "Editando sesión: {date}",
    "visibility": "Visibilidad: {date}",
    "altitude_deg": "Altitud (°)",
    "recommendation": "Recomendación: {pixels} px",
    "time_limit_reached": "(Límite de tiempo alcanzado)",
    "max_real_subs": "(Máx {max} | Real {real})",

    // ========================================================================
    // BASE JS / GLOBAL
    // ========================================================================
    "loading_help_content": "Cargando contenido de ayuda...",
    "help_content_empty": "Error: El contenido de ayuda devuelto está vacío.",
    "network_error_help": "Error de red: No se pudo cargar el tema de ayuda '{topic}'.",
    "latest_version": "Última versión: v{version}",

    // ========================================================================
    // MESSAGES / ALERTS
    // ========================================================================
    "saved_successfully": "Guardado correctamente",
    "deleted_successfully": "Eliminado correctamente",
    "confirm_delete": "¿Está seguro de que desea eliminar esto?",
    "unsaved_changes": "Tiene cambios sin guardar. ¿Está seguro de que desea salir?",
    "network_error": "Error de red. Por favor intente de nuevo.",
    "session_expired": "Su sesión ha expirado. Por favor inicie sesión de nuevo.",
    "error_with_message": "Error: {message}",
    "failed_with_error": "Falló: {error}",

    // ========================================================================
    // CHART / GRAPH
    // ========================================================================
    "altitude_chart": "Gráfico de altitud",
    "altitude_degrees": "Altitud (°)",
    "time_hours": "Tiempo (horas)",
    "tonight": "Esta noche",
    "show_framing": "Mostrar encuadre",
    "hide_framing": "Ocultar encuadre",
    "horizon": "Horizonte",
    "moon_altitude": "Altitud lunar",
    "object_altitude": "Altitud de {object}",

    // ========================================================================
    // HELP / ABOUT
    // ========================================================================
    "help": "Ayuda",
    "about": "Acerca de",
    "about_nova_dso_tracker": "Acerca de Nova DSO Tracker",

    // ========================================================================
    // MISC / PLACEHOLDERS
    // ========================================================================
    "select_language": "Seleccionar idioma",
    "toggle_theme": "Cambiar tema",
    "toggle_dark_light_theme": "Alternar tema oscuro/claro"
};
