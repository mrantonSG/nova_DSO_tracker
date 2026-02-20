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
