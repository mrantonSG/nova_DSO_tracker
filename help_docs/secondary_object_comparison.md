# Secondary Object Comparison

The **Add Object** dropdown lets you compare the altitude curve of another object alongside your primary target on the graph view.

## How Objects Are Ranked

Objects in the dropdown are **sorted by total observable duration** (longest first). This helps you quickly identify which active targets are visible for the longest time tonight.

## Filtering Criteria

Objects appear in the list only if they meet **all** of the following:

1. **Active Project** — The object must be marked as an Active Project (checkbox in the Notes & Framing tab)
2. **Observable Tonight** — The object must be above your horizon mask during astronomical darkness
3. **Valid Coordinates** — The object must have RA and DEC coordinates defined
4. **Not the Primary** — The object you're currently viewing is excluded from the list

## Limits

- Only the **top 20** objects by observable duration are shown
- Objects with **0 minutes** of observable time are excluded

## Display

- The secondary object's altitude is shown as a **solid magenta line**
- No azimuth line is rendered for the secondary object (to reduce chart clutter)
- Selecting **None** removes the secondary comparison and restores the default view

## Tips

- Use this feature to plan your night by comparing multiple targets
- Great for deciding between objects that transit at different times
- The page title updates to show both object names when a comparison is active
