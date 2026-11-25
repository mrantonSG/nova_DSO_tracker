/**
 * utils.js
 * Shared utility functions used across multiple pages
 */

/**
 * Format ISO date string (YYYY-MM-DD) to European format (DD.MM.YYYY)
 * @param {string} isoStr - ISO date string
 * @returns {string} European formatted date or 'N/A' if invalid
 */
function formatDateISOtoEuropean(isoStr) {
  if (!isoStr || typeof isoStr !== 'string') return 'N/A';
  const parts = isoStr.split("-");
  if (parts.length !== 3) {
    console.warn("formatDateISOtoEuropean received unexpected format:", isoStr);
    return isoStr;
  }
  const [year, month, day] = parts;
  return `${day}.${month}.${year}`;
}

/**
 * True when an image/resource URL points to another origin (catalog image,
 * DSS2, Wikipedia). Same-origin URLs (uploads) and data:/blob: URLs are not
 * external: they can load while the browser is offline, so callers should
 * only treat "offline" as "unavailable" for external URLs.
 * @param {string} url
 * @returns {boolean}
 */
function isExternalUrl(url) {
  if (!url) return false;
  try {
    const u = new URL(url, window.location.href);
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.origin !== window.location.origin;
  } catch (e) {
    return false;
  }
}

function loadPlotly() {
  return new Promise(function(resolve, reject) {
    if (window.Plotly) { resolve(); return; }
    var s = document.createElement('script');
    s.src = window._plotlySrc;
    s.onload = resolve;
    s.onerror = function() { reject(new Error('Plotly failed to load')); };
    document.head.appendChild(s);
  });
}
