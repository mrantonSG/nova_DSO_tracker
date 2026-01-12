
# Nova DSO Tracker v3.4.0 Release Notes

**Release Date:** September 21, 2025

I'm excited to announce the release of Nova DSO Tracker v3.4.0\! This is a focused release that introduces a powerful and highly-requested feature: **External User Management & Sync**. This makes Nova ideal for astronomy clubs, shared observatories, and teams.

-----

## New Feature: External User Management & Sync

You can now manage all user accounts in an external system (like a WordPress site) and have Nova DSO Tracker automatically sync with it. This eliminates the need to create or maintain user accounts directly inside Nova.

### Why It's a Game-Changer:

  - **Single Source of Truth**: Manage your users in one place. If you already have a club website with memberships, Nova now integrates seamlessly.
  - **No More Duplicate Admin**: When a user registers, updates their profile, or is removed from your main system, Nova automatically stays in sync. No manual intervention is needed.
  - **Effortless Onboarding**: New users sign up on your main site and can immediately log into Nova. Their personal configs, rigs, and journals are created for them automatically on their first login.
  - **Scalable for Groups**: Perfect for organizations that need to provide separate, secure access to Nova for each member.

-----

## How to Get Started

Configuring the new multi-user mode is straightforward. The basic steps are:

1.  **Set Up Your External System**: Ensure you have a working login and registration flow on your website (e.g., a WordPress site with the *User Registration* plugin).
2.  **Configure Nova**: In your `instance/` folder, edit the `.env` file and make sure the following line is set:
    ```ini
    SINGLE_USER_MODE=False
    ```
3.  **Restart Nova**: Once the setting is saved, restart the Nova application.

That's it\! Nova will now defer to your external system for authentication. When a recognized user accesses Nova, it will automatically load or create their personal `config_<username>.yaml` and `journal_<username>.yaml` files.

For a complete walkthrough, please refer to our **Admin Setup Guide**.

-----

## Other Improvements

  - This release also includes minor under-the-hood improvements for stability and performance in multi-user environments.

-----

Thank you for your continued support and feedback. We're thrilled to see how this new feature empowers clubs and teams in their astronomical pursuits.

Happy observing\!