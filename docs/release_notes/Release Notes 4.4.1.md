
# Nova DSO Tracker v4.4.1 - Maintenance & Polish

Hi everyone!

After the last release, I noticed a few things that were not behaving quite right (and a few of you noticed them too—thanks for the heads up!). Version 4.4.1 is mostly about tightening the bolts and fixing some bugs that slipped past me.

Here is what I have been working on:

### The Improvements

* **Better Reports:** I have updated the Project Reports so they now include the individual Session Reports automatically. I also tweaked the page break logic in Session Reports to match the Project ones, so your PDFs should look much cleaner and professional now.
* **Journal Behavior:** I made some changes to how the iframe handles the journal view. It should be a smoother experience when you are browsing through your logs.

### Fixes (Pardon the Mess!)

* **The "Stuck" Message:** Fixed a bug where the "calculation" message would sometimes freeze and refuse to go away. Sorry if that left you wondering if the app had crashed!
* **Heatmap Error:** Squashed a bug where the Heatmap was throwing a "Can't find variable: Plotly" error. That was my bad, but it should be graphing happily now.
* **Missing Notes:** I realized that note content was not actually rendering in the graph HTML view. I have reconnected the plumbing there, so your notes will be visible where they are supposed to be.

### A Quick Note

As always, I am just one person working on this in my spare time, so I really appreciate your patience when these little bugs pop up. Thank you so much for using Nova and for sticking with me as I learn and improve the software.

Clear skies!