Version 4.3.0 is ready to go. I have focused this update on better long-term planning tools and improving data accuracy.

### New Stuff

**The Yearly Heatmap**
I have added a new "Waterfall Heatmap" visualization to help you assess target visibility over the next 12 months.
* The chart uses a color scale where darker green indicates better imaging quality, while white vertical bands highlight full moon periods.
* To keep the interface responsive, the data loads in chunks rather than all at once.

**Integrated Filtering**
To make the heatmap easier to read, I have integrated the "Saved Views" functionality directly into the visualization. You can now apply your custom filters to the heatmap to narrow down your targets.
* I also added an "Active Only" checkbox, allowing you to quickly filter the view to show only your current active projects.

### Improvements

**Simbad Coordinate Import**
I updated the import logic for Simbad to handle Right Ascension (RA) and Declination (DEC) values more reliably. The system now correctly detects and converts degree values into hours to ensure your target coordinates are accurate.

### Under the Hood

**Automated Testing**
To ensure the stability of the new Simbad logic, I have added a set of automated test cases. These tests verify that coordinate conversions are handled correctly for various input formats.

***

Thank you for using Nova. I appreciate the support from the community. Clear skies!