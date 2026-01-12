
# Nova DSO Tracker v4.4.2

This is a small but important hotfix to address a confusing issue where the visual data and the text data weren't quite speaking the same language.

## The Quick Fix (Sorry about the confusion!)

* **Imaging Day Synchronization:** I realized that the graph and the data table were having a bit of a disagreement on what "today" meant, especially if you were checking the app in the morning hours.
    * The issue was in how the "observing night" (the noon-to-noon logic) was calculated between the API and the dashboard view.
    * I've tightened up the logic so both the Altitude Plot and the Data Table now consistently agree on the current imaging session date. No more seeing a curve for tonight while the table shows data for tomorrow!

## A Quick Note
That's pretty much it for this one. I just wanted to get this patched quickly so your planning sessions are accurate.

***

Thanks again for using Nova and for bearing with me. It means a lot to have such a supportive community for this little passion project of mine!
