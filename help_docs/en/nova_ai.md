# Nova AI

Nova AI ranks your object list using artificial intelligence, analyzing current conditions, your location, and your rigs to recommend the best targets for tonight.

### How to Use
1.  **Click "Ask Nova":** The button is located in the dashboard toolbar, next to the search field.
2.  **Wait for Results:** The AI typically takes about 50 seconds to analyze your full target list and generate rankings.
3.  **View Ranked List:** Objects will be sorted by their AI score, with the highest-ranked targets at the top.

### Controls
* **Restore Nova:** If you've already asked Nova today, this button instantly restores the cached ranking without making a new API call. This is useful if you've changed filters or views.
* **Re-ask:** Forces a fresh AI query, replacing the cached result. Use this when conditions have changed significantly or you want updated rankings.
* **Remove Filter:** Resets your view (removes any applied filters and sorting) while preserving the cached ranking. Use this to see the full ranked list after exploring a filtered subset.

### Cache Behavior
* Results are cached per location per day.
* The cache is automatically invalidated when you change the date or location.
* This means rankings persist across filter changes and page navigation until the next day or location switch.
