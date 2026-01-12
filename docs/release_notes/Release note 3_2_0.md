
***

## Nova DSO Tracker 3.2.0 Released: The Starlight Update 

I'm thrilled to announce the release of **Nova DSO Tracker version 3.2.0**! This is one of the biggest updates yet, focusing on a complete user experience overhaul, powerful new planning tools, and significant performance enhancements under the hood. The entire application has been refined to be faster, more intuitive, and more beautiful to use.

Thank you for your continued support. Now, let's dive into what's new!

---

### Major New Features

#### **1. All-New Interactive Framing Assistant**
The framing tool has been completely rebuilt from the ground up into a professional-grade planning window.

* **Modern Interface:** A sleek, fullscreen-capable modal window provides a massive canvas for planning your shots.
* **Survey Blending:** You can now blend a secondary survey (like DSS2 Red) over your primary image survey with an opacity slider to highlight specific features like Hα regions.
* **Image Adjustments:** Fine-tune the view with real-time controls for Brightness, Contrast, Gamma, and Saturation.
* **Precise Controls:**
    * **Lock FOV:** Keep your field of view perfectly centered while you pan and zoom the sky behind it.
    * **Nudge & Rotate:** Use a smooth rotation slider or nudge buttons (1' steps) to dial in the perfect composition.
    * **Shareable URLs:** Copy a unique URL that saves your exact rig, rotation, and custom RA/Dec center to share or bookmark.
    * **Insert to Project:** Automatically append a detailed, formatted framing plan (including the shareable URL) directly into your project notes.


#### **2. Interactive, Client-Side Altitude Charts**
Altitude graphs are no longer static images! They are now rendered directly in your browser using Chart.js for a fluid and responsive experience.

* **Day, Month & Year Views:** Instantly switch between the classic 24-hour altitude plot, a monthly view of altitude at midnight, and a full yearly overview.
* **Fast:** Date changes and view switches are now instantaneous with no need to reload or wait for a new image to be generated.
* **Improved Readability:** A cleaner design with improved annotations for astronomical events makes the charts easier to read than ever.

#### **3. Nyquist Sampling Advisor for Rigs**
Take the guesswork out of matching your equipment to the night sky. On the "Rigs" configuration tab, you can select your typical seeing conditions (e.g., "Good Seeing, 2.0" - 4.0" FWHM").

* Nova will instantly analyze each of your configured rigs against this seeing value.
* It provides **color-coded feedback** on your sampling rate (e.g., Oversampled, Good, Undersampled).
* For oversampled rigs, it even provides a helpful tip, showing what your image scale and sampling rate would be if you used 2x2 binning.


---

###  UI & UX Enhancements

* **Complete UI Refresh:** The application has a new, modern graphic design. Configuration pages now use a cleaner, frameless layout, and the object details page has been reorganized for better clarity and information flow.
* **Calculation Precision Control:** A new "Calculation Precision" setting on the General tab allows you to adjust the time interval used for nightly calculations. Faster settings are ideal for low-power devices like a Raspberry Pi, while high-precision settings refine the accuracy of observable duration and max altitude values.

---

### Performance & Under-the-Hood

* **Faster Main Page:** The data loading mechanism for the main tracker page has been completely rewritten. It now uses an asynchronous API to fetch data for each object individually, leading to a dramatically faster and more responsive feel, especially for users with long object lists.
* **Smarter Caching:** New caching layers have been added for journal and rig configuration files, reducing disk reads and speeding up page loads throughout the application.
* **Automated Data Migration:** For existing users, a one-time migration automatically calculates and adds the total integration time to all previous journal entries, ensuring they are fully compatible with the new features.

***

Thank you for being a part of the Nova community. I can't wait for you to try out these new features!

Clear skies!