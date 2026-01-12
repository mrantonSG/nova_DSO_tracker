
---

## Nova DSO Tracker 3.7.0: The Workflow Update

I'm excited to announce the release of **Nova DSO Tracker version 3.7.0**! This is a massive update focused on improving the usability of nova, organizing your imaging sessions, and giving you more control over your valuable data than ever before.

This release introduces powerful new ways to manage projects, track your progress visually, and ensure your data stays in sync across installations. Let's dive into what's new!

### Highlights of Version 3.7.0

* **Visual Session Journals**: Your imaging journal is now a visual logbook! You can upload a representative image for each session, making it easier than ever to review your progress.
* **Active Projects & Outlook Integration**: Prioritize your targets with the new "Active Project" checkbox. The main "Outlook" tab now automatically finds the best upcoming imaging opportunities for all your active projects, helping you plan your next session with confidence.
* **Enhanced Location Management**: For those with multiple imaging sites, you can now set locations as "Active" or "Inactive." This cleans up your main interface, ensuring only relevant sites appear in the selection dropdowns. You're now also able to add horizon masks and comments right when you create a new location.
* **Full Data Portability**: Easily back up and synchronize your entire journal between different Nova installations. A new feature allows you to download a single `.zip` archive of all your journal photos and seamlessly import it elsewhere.

---

### Complete Changelog

#### New Features & Major Enhancements

* **Journal Photo Uploads**: You can now upload a JPG, PNG, or GIF for each journal session to create a visual history of your imaging progress.
* **Active Projects**: A simple checkbox on the "Framing & Notes" tab lets you mark an object as an "Active Project."
* **Outlook for Active Projects**: The main "Outlook" tab is now powered by your Active Projects list, giving you a powerful tool to see the best upcoming nights for your primary targets.
* **Project-Based Session Grouping**: On the object detail page, the journal history can now  be grouped by project, including a running total of the integration time for each.
* **Full Journal Photo Sync**: You can now download a `.zip` archive of all your journal photos and import that archive into another Nova instance, making synchronization a breeze.
* **Location Management**:
    * Locations can be marked as "Active" or "Inactive." Only active locations appear in the main location dropdown, reducing clutter.
    * A limit of 5 active locations has been introduced to keep performance snappy.
    * You can now add comments and a horizon mask directly when creating a new location.
* **Opportunities Tab**: The "Imaging Opportunities" section has been promoted to its own tab on the graph dashboard for better visibility.

#### Improvements & UI Polish

* **Improved Navigation**: Clicking a session in the main Journal tab or the index page now takes you directly to the "Journal" tab on that object's detail screen.
* **Better Offline Handling**: When you're not connected to the internet, the SIMBAD and Framing Assistant tabs now display a clear, helpful message instead of failing silently.
* **Smarter Loading Indicators**: The loading animation for the "Opportunities" tab now appears neatly within the table, providing a cleaner user experience.
* **Helpful Map Links**: Each location on the configuration page now includes a direct link to view its coordinates on a map.

#### Bug Fixes & Robustness

* **Orphaned Project Cleanup**: The system now automatically cleans up project names from your journal if no sessions are linked to them, keeping your data tidy.
* **Column Widths Fixed**: Column widths in the Opportunities table have been adjusted for better readability.

---

Thank you for your continued support and feedback, which have been invaluable in shaping this release. I hope these new features help you plan and execute your imaging sessions more effectively than ever before.

Clear skies!