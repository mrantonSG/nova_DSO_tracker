
# Nova DSO Tracker v4.8.2

This is a maintenance release focused on keeping the application secure and fixing a calculation bug in the dashboard.

## Security Fixes

I have updated several critical libraries to resolve high and moderate-severity vulnerabilities. Keeping these dependencies up to date ensures the tracker stays safe for everyone to use.

* **setuptools:** Upgraded to 78.1.1 to fix a path traversal vulnerability in PackageIndex.download (CVE-2024-47873).
* **jaraco.context:** Upgraded to 6.1.0 to resolve a path traversal vulnerability discovered earlier this year.
* **Werkzeug:** Upgraded to 3.1.3 to patch a Windows-specific device name handling issue.
* **Requests:** Upgraded to 2.32.3 to fix a potential credential leak via .netrc.
* **Flask:** Upgraded to 3.1.0 to address a fallback key signing issue.
* **fontTools:** Upgraded to 4.56.0 to mitigate XML injection risks.

## Bug Fixes

* **Dashboard Simulation:** I fixed an error where the angular separation was being calculated incorrectly while in simulation mode. The values should now be accurate when you are planning future sessions.

---

Thanks again for all your patience and feedback, it really keeps this project going.