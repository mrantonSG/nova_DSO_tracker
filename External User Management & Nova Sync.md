
# External User Management & Nova Sync

## What it is

Nova DSO Tracker supports external user management, meaning you don’t have to create or maintain accounts directly inside Nova. Instead, you can manage all users in an external system (like WordPress, or any service that provides a login/registration flow). Nova then syncs automatically, ensuring that accounts remain consistent between the two systems.

-----

## Why this exists

  - Many astrophotography setups already run a website or membership portal (e.g., WordPress).
  - Duplicating user management inside Nova would mean two separate login systems, which quickly gets messy.
  - With this feature, Nova acts as a "follower": users are created, updated, or removed in the external system, and Nova stays in sync.

-----

## How it works

  - **External System Handles Logins**
      - You add login & registration forms on your website (e.g., `[user_registration_login]` in WordPress).
      - Users register and authenticate through that system.
  - **Nova Reads from Environment & Config**
      - Nova’s `.env` and YAML config files include flags like:
          - `SINGLE_USER_MODE` or multi-user database mode.
          - `INSTANCE_ID` to identify your installation.
          - (Optional) API keys/tokens if syncing via an integration.
  - **On Startup & Sync**
      - Nova looks up external users (via API, config, or database sync).
      - If a user is missing internally, Nova creates a default config + journal from templates.
      - If a user is disabled externally, Nova won’t allow login.
  - **User Experience**
      - End users log in via your external system (e.g., WordPress site).
      - Nova simply respects the session and loads their personal configs, objects, rigs, and journals automatically.

-----

## Typical Setup

  - **In WordPress:**
      - Create "Login" and "Register" pages with the shortcodes.
      - Style them to match your site.
      - Add the login button to the navigation menu.
  - **In Nova:**
      - Set `SINGLE_USER_MODE=False` so multi-user handling is active.
      - Ensure your `.env` is configured with `SECRET_KEY` and `INSTANCE_ID`.
      - Start Nova normally—it will map incoming sessions to user configs in `instance/configs/`.

-----

## Benefits

  - **One source of truth**: All users are managed in WordPress (or your external system).
  - **No duplicate admin effort**: Nova always stays in sync automatically.
  - **Cleaner UX**: Users sign up once and immediately get their own Nova configs/journals.
  - **Scalable**: Suitable for clubs, teams, or shared observatories.

-----

# Admin Setup Guide

This guide shows how to configure Nova DSO Tracker so that user accounts are managed in WordPress (or another external system), while Nova simply syncs and provides each user with their own configs, rigs, and journals.

-----

## 1\. Prerequisites

  - A working WordPress site with the **User Registration** plugin (or another plugin that provides `[user_registration_login]` and `[user_registration_form]` shortcodes).
  - A working Nova DSO Tracker installation (Docker, Pi, or manual).
  - Access to Nova’s `.env` file and `instance/configs/` folder.

-----

## 2\. Set up Login & Registration in WordPress

1.  **Create a Login Page**

      - Add a new page in WordPress called "Login".
      - Insert the shortcode:
        ```html
        [user_registration_login]
        ```
      - Publish it.
      - Add this page to your site’s navigation menu.

2.  **Create a Registration Page**

      - Add another page called "Join" or "Register".
      - Insert the shortcode:
        ```html
        [user_registration_form]
        ```
      - Publish it.
      - Optionally add it to your navigation menu or link from a "Join" button.

3.  **Test the Flow**

      - Create a new account in WordPress to confirm that the login and logout process works correctly.

-----

## 3\. Configure Nova for Multi-User Mode

Nova needs to know that it should not run in single-user mode.

1.  Open the `.env` file inside Nova’s `instance/` folder.

2.  Make sure you have these lines set correctly:

    ```ini
    SECRET_KEY=your-random-secret
    INSTANCE_ID=auto-generated-or-custom
    SINGLE_USER_MODE=False
    ```

      - `SECRET_KEY`: This is set automatically on first run. Don’t change it unless you need to regenerate it.
      - `INSTANCE_ID`: The unique ID of your Nova installation.
      - `SINGLE_USER_MODE`: This **must be `False`**.

3.  Restart Nova for the changes to take effect.

-----

## 4\. How Nova Handles Users

  - When a new user logs in (via WordPress), Nova:
      - Creates a config and journal file for them in the `instance/configs/` directory:
        ```bash
        instance/configs/config_<username>.yaml
        instance/configs/journal_<username>.yaml
        ```
      - Copies the templates (`config_default.yaml`, `journal_default.yaml`) to create these new files if they don’t already exist.
      - Loads these personal configs every time that user logs in.
  - If a user is disabled or deleted in WordPress, they will no longer be able to log into Nova.

-----

## 5\. Admin Tips

  - **Customize Defaults**: Edit `config_default.yaml` and `journal_default.yaml` in the `config_templates/` folder. These templates are used for every new user.
  - **Backups**: User configs and journals live under `instance/configs/`. Remember to back up this directory regularly.
  - **Guest Access**: You can configure `config_guest_user.yaml` to provide a read-only or limited experience for non-registered "guest" viewers.
  - **Diagnostics**: If something isn't working, check the Flask logs when a new user logs in. Nova prints whether a config/journal was successfully created or found.

-----

## 6\. Example Workflow

1.  You run a WordPress site for your astronomy club.
2.  Members sign up on your site.
3.  The next time they open Nova DSO Tracker, their account is already recognized.
4.  Nova automatically creates their own config (for locations, objects, rigs) and a personal journal file.
5.  Each member manages their own observing logs separately, with no admin intervention needed.

-----

**Done\!** Now WordPress is the source of truth, Nova stays in sync, and you never have to manage user accounts in two places.