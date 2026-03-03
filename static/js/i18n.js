/**
 * Nova DSO Tracker - JavaScript i18n Loader
 *
 * This module loads the appropriate language file based on the user's
 * language preference and exposes a global translation function.
 *
 * Usage:
 *   window.t('key') - Returns translated string or key if not found
 *
 * The language files are in static/js/i18n/{lang}.js and define
 * window.NOVA_I18N[lang] as a flat key/value object.
 */
(function() {
    'use strict';

    // Default language
    var DEFAULT_LANGUAGE = 'en';

    // Get the user's language preference from Nova config
    function getUserLanguage() {
        if (window.NOVA_CONFIG && window.NOVA_CONFIG.language) {
            return window.NOVA_CONFIG.language;
        }
        return DEFAULT_LANGUAGE;
    }

    // Translation function - exposed globally
    window.t = function(key, substitutions) {
        var lang = getUserLanguage();
        var translations = window.NOVA_I18N || {};

        // Try the user's language first, fall back to English, then to the key itself
        var value = (translations[lang] && translations[lang][key])
                 || (translations['en'] && translations['en'][key])
                 || key;

        // Handle substitutions like t('greeting', { name: 'John' })
        // Replaces {name} with substitutions.name
        if (substitutions && typeof value === 'string') {
            Object.keys(substitutions).forEach(function(placeholder) {
                value = value.replace(
                    new RegExp('\\{' + placeholder + '\\}', 'g'),
                    substitutions[placeholder]
                );
            });
        }

        return value;
    };

    // Pluralization helper
    // window.tn('key', 'key_plural', count, substitutions)
    window.tn = function(singularKey, pluralKey, count, substitutions) {
        var key = (count === 1) ? singularKey : pluralKey;
        var subs = Object.assign({}, substitutions || {}, { count: count });
        return window.t(key, subs);
    };

    // Log initialization (helps debugging)
    console.log('[i18n] Initialized with language:', getUserLanguage());

})();
