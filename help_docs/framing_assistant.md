
#### Framing Assistant

The **Framing Assistant** is a powerful visual tool that lets you preview exactly how your target will look through your camera. It overlays your equipment's field of view (FOV) onto a professional sky survey image.

**Getting Started**

1.  **Select a Rig:** Use the dropdown at the top to choose one of your pre-configured equipment profiles. The rectangle on the screen represents your sensor's field of view.
2.  **Move & Center:**
    * **Click & Drag:** By default, **Lock FOV** is enabled. This means the sensor rectangle stays fixed in the center of your screen, while dragging the mouse moves the sky *behind* it. This simulates how your telescope moves across the sky to frame the target.
    * **Nudge Controls:** (Lock FOV off) Use the arrow buttons (↑ ↓ ← →) in the toolbar (or your keyboard arrow keys) to make fine adjustments in 1-arcminute steps.
    * **Recenter:** Click "Recenter to object" to snap the view back to the target's catalog coordinates.

**Composition Controls**

* **Rotation:** Use the slider to rotate your camera angle (0-360°).
    * **ASIAIR Compatibility:** The angle shown here correlates exactly with the "Framing Support" angle in the **ASIAIR** app, making it easy to replicate your plan in the field.
    * *Tip:* Tap the angle text next to the slider to quickly reset it to 0°.
* **Surveys:** Change the background image source using the "Survey" dropdown.
    * **DSS2 (Color):** Good general-purpose optical view.
    * **H-alpha:** Excellent for seeing faint nebulosity structure.
    * **Blend Mode:** You can blend a second survey (like H-alpha) over the base color image using the "Blend with" dropdown and opacity slider. This helps reveal hidden details while keeping star colors visible.

**Mosaic Planner**

If your target is too big for a single frame, use the **Mosaic** section in the toolbar.

1.  Set the number of **Columns** and **Rows** (e.g., 2x1 for a wide panorama).
2.  Adjust the **Overlap %** (default is 10%).
3.  **Copy Plan:** Click "Copy Plan (CSV)" to generate a coordinate list compatible with acquisition software like **ASIAIR**, or **N.I.N.A.**.

**Saving Your Work**

* **Save Framing:** Click this to store your current Rig, Rotation, and Center Coordinates to the database. The next time you visit this object, your custom framing will be restored automatically.
* **Lock FOV:** This is checked by default. Unchecking it unlocks the sensor rectangle, allowing you to drag the rectangle itself around a static sky map.
