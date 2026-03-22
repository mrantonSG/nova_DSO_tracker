# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false, reportArgumentType=false, reportCallIssue=false
import getpass
import os
import re
import traceback

from nova.config import SINGLE_USER_MODE, TEMPLATE_DIR
from nova.models import (
    Base,
    engine,
    SessionLocal,
    DbUser,
    Role,
    Permission,
    AstroObject,
    JournalSession,
    Rig,
    Component,
    Project,
    SavedFraming,
    SavedView,
    Location,
    UiPref,
)


def register_cli(app):
    from nova import (
        initialize_instance_directory,
        run_one_time_yaml_migration,
        get_db,
        _seed_user_from_guest_data,
        _read_yaml,
        _migrate_locations,
        _migrate_objects,
        _migrate_ui_prefs,
        _migrate_saved_framings,
        _migrate_saved_views,
        _migrate_components_and_rigs,
        _migrate_journal,
    )

    if not SINGLE_USER_MODE:

        @app.cli.command("init-db")
        def init_db_command():
            """Creates database tables and the first admin user."""
            # Create the tables using the engine from nova.models
            Base.metadata.create_all(engine)
            print("✅ Initialized the database tables.")

            db_sess = SessionLocal()
            try:
                # Check if a user already exists to prevent running this twice
                if db_sess.query(DbUser).first():
                    print(
                        "-> Database already contains users. Skipping admin creation."
                    )
                    return

                # If no users exist, prompt to create the first one
                print("--- Create First Admin User ---")
                username = input("Enter username for admin: ")
                password = getpass.getpass("Enter password for admin: ")

                # Create the user object and save it to the database
                admin_user = DbUser(username=username)
                admin_user.set_password(password)
                # Assign admin role if Role table exists
                admin_role = db_sess.query(Role).filter_by(name="admin").first()
                if admin_role:
                    admin_user.roles.append(admin_role)
                db_sess.add(admin_user)
                db_sess.commit()
                print(f"✅ Admin user '{username}' created successfully!")
            finally:
                db_sess.close()

        @app.cli.command("seed-roles")
        def seed_roles_command():
            """Creates system roles and permissions for RBAC."""
            from nova.permissions import SYSTEM_PERMISSIONS, DEFAULT_ROLE_PERMISSIONS

            db_sess = SessionLocal()
            try:
                # Check if roles already exist
                existing_roles = db_sess.query(Role).count()
                if existing_roles > 0:
                    print(f"-> Found {existing_roles} existing roles. Skipping seed.")
                    return

                # Create all permissions from the central definition
                perms = {}
                for name, desc in SYSTEM_PERMISSIONS:
                    p = Permission(name=name, description=desc)
                    db_sess.add(p)
                    perms[name] = p
                db_sess.flush()
                print(f"✅ Created {len(perms)} permissions.")

                # Create admin role with all permissions
                admin_role = Role(
                    name="admin", description="Full system access", is_system=True
                )
                admin_role.permissions = list(perms.values())
                db_sess.add(admin_role)

                # Create user role with standard permissions
                user_role = Role(
                    name="user", description="Standard user", is_system=True
                )
                user_perms = DEFAULT_ROLE_PERMISSIONS.get("user", [])
                user_role.permissions = [perms[p] for p in user_perms if p in perms]
                db_sess.add(user_role)

                # Create readonly role with view-only permissions
                readonly_role = Role(
                    name="readonly", description="Read-only access", is_system=True
                )
                readonly_perms = DEFAULT_ROLE_PERMISSIONS.get("readonly", [])
                readonly_role.permissions = [
                    perms[p] for p in readonly_perms if p in perms
                ]
                db_sess.add(readonly_role)
                db_sess.flush()
                print("✅ Created 3 system roles: admin, user, readonly.")

                # Assign admin role to any user named 'admin'
                admin_user = db_sess.query(DbUser).filter_by(username="admin").first()
                if admin_user:
                    admin_user.roles.append(admin_role)
                    print(f"✅ Assigned admin role to user: {admin_user.username}")

                # Assign user role to all other active users without roles
                other_users = (
                    db_sess.query(DbUser)
                    .filter(DbUser.username != "admin", DbUser.active == True)
                    .all()
                )
                for u in other_users:
                    if not u.roles:
                        u.roles.append(user_role)
                if other_users:
                    print(
                        f"✅ Assigned user role to {len(other_users)} existing users."
                    )

                db_sess.commit()
                print("✅ RBAC seed complete!")
            finally:
                db_sess.close()

        @app.cli.command("add-user")
        def add_user_command():
            """Creates a new user account."""
            print("--- Create New User ---")
            username = input("Enter username: ")

            db_sess = SessionLocal()
            try:
                # Check if username already exists
                if db_sess.query(DbUser).filter_by(username=username).first():
                    print(f"❌ User '{username}' already exists.")
                    return

                password = getpass.getpass("Enter password: ")
                confirm = getpass.getpass("Confirm password: ")
                if password != confirm:
                    print("❌ Passwords do not match.")
                    return

                user = DbUser(username=username)
                user.set_password(password)
                # Assign default 'user' role if Role table exists
                user_role = db_sess.query(Role).filter_by(name="user").first()
                if user_role:
                    user.roles.append(user_role)
                db_sess.add(user)
                db_sess.commit()
                print(f"✅ User '{username}' created successfully!")
            finally:
                db_sess.close()

        @app.cli.command("rename-user")
        def rename_user_command():
            """Renames an existing user account."""
            old_name = input("Current username: ")

            db_sess = SessionLocal()
            try:
                user = db_sess.query(DbUser).filter_by(username=old_name).first()
                if not user:
                    print(f"❌ User '{old_name}' not found.")
                    return

                new_name = input("New username: ").strip()
                if not new_name:
                    print("❌ Username cannot be empty.")
                    return

                if db_sess.query(DbUser).filter_by(username=new_name).first():
                    print(f"❌ Username '{new_name}' is already taken.")
                    return

                user.username = new_name
                db_sess.commit()
                print(f"✅ User renamed from '{old_name}' to '{new_name}'.")
            finally:
                db_sess.close()

        @app.cli.command("change-password")
        def change_password_command():
            """Changes the password for an existing user."""
            username = input("Username: ")

            db_sess = SessionLocal()
            try:
                user = db_sess.query(DbUser).filter_by(username=username).first()
                if not user:
                    print(f"❌ User '{username}' not found.")
                    return

                password = getpass.getpass("New password: ")
                confirm = getpass.getpass("Confirm new password: ")
                if password != confirm:
                    print("❌ Passwords do not match.")
                    return

                user.set_password(password)
                db_sess.commit()
                print(f"✅ Password changed for '{username}'.")
            finally:
                db_sess.close()

        @app.cli.command("delete-user")
        def delete_user_command():
            """Deletes a user account from the database."""
            username = input("Username to delete: ")

            db_sess = SessionLocal()
            try:
                user = db_sess.query(DbUser).filter_by(username=username).first()
                if not user:
                    print(f"❌ User '{username}' not found.")
                    return

                if user.is_admin:
                    print("❌ Cannot delete an admin account.")
                    return

                confirm = input(
                    f"Are you sure you want to delete '{username}'? (yes/no): "
                )
                if confirm.lower() != "yes":
                    print("Cancelled.")
                    return

                db_sess.delete(user)
                db_sess.commit()
                print(f"✅ User '{username}' deleted.")
            finally:
                db_sess.close()

        @app.cli.command("migrate-yaml-to-db")
        def migrate_yaml_command():
            """
            Initializes instance directories and runs the one-time migration
            from all YAML files to the app.db database.
            """
            print("--- [MIGRATION COMMAND] ---")
            print("Step 1: Initializing instance directory...")
            initialize_instance_directory()
            print("Step 2: Running YAML to Database migration...")
            run_one_time_yaml_migration()
            print("--- [MIGRATION COMMAND] ---")
            print("✅ Migration task complete.")

    @app.cli.command("seed-empty-users")
    def seed_empty_users_command():
        """
        Finds all existing users with no data (no locations) and seeds
        their accounts from the 'guest_user' template.
        """
        print("--- [BACKFILL SEEDING EMPTY USERS] ---")
        db = get_db()
        try:
            # Find all users *except* the system/template users
            users_to_check = (
                db.query(DbUser)
                .filter(DbUser.username != "default", DbUser.username != "guest_user")
                .all()
            )

            if not users_to_check:
                print("No user accounts found to check.")
                return

            print(f"Found {len(users_to_check)} user account(s) to check...")
            seeded_count = 0

            for user in users_to_check:
                # We will check and seed each user in their OWN transaction
                # This is safer for a live system.
                try:
                    # The seeding function already contains the safety check
                    # to see if the user is empty.
                    print(f"Checking user: '{user.username}' (ID: {user.id})...")
                    _seed_user_from_guest_data(db, user)

                    # If the function added data, the session will be "dirty"
                    if db.is_modified(user) or db.new or db.dirty:
                        db.commit()
                        print(f"   -> Successfully seeded '{user.username}'.")
                        seeded_count += 1
                    else:
                        # This happens if the safety check was triggered
                        db.rollback()  # Rollback any potential flushes

                except Exception as e:
                    db.rollback()
                    print(
                        f"   -> FAILED to seed '{user.username}'. Rolled back. Error: {e}"
                    )

            print("--- [BACKFILL COMPLETE] ---")
            print(f"✅ Successfully seeded {seeded_count} empty user account(s).")

        except Exception as e:
            db.rollback()
            print(f"❌ An unexpected error occurred: {e}")
            traceback.print_exc()
        finally:
            db.close()

    @app.cli.command("repair-image-links")
    def repair_image_links_command():
        """
        Finds and repairs image URLs in Trix content.
        1. Converts absolute URLs (e.g., 'http://localhost/...') to
           portable relative URLs (e.g., '/uploads/...').
        2. If in SINGLE_USER_MODE, also rewrites all user-specific paths
           (e.g., '/uploads/mrantonSG/...') to the 'default' user path
           (e.g., '/uploads/default/...').
        """
        print("--- [REPAIRING BROKEN IMAGE LINKS (v2)] ---")
        db = get_db()

        # Regex 1: Fixes absolute URLs (e.g., http://.../uploads/...)
        # Groups: (1: http://host) (2: /uploads/user/img.jpg) (3: quote)
        abs_url_pattern = re.compile(r'(http[s]?://[^/"\']+)(/uploads/.*?)(["\'])')
        abs_replacement = r"\2\3"  # Replace with: /uploads/user/img.jpg"

        # Regex 2: Fixes user paths *only* if in Single-User Mode
        su_url_pattern = None
        su_replacement = ""
        if SINGLE_USER_MODE:
            print(
                "--- Running in Single-User Mode: Will also fix user paths to 'default' ---"
            )
            # Groups: (1: /uploads/) (2: !default) (3: /img.jpg) (4: quote)
            # This regex finds any path in /uploads/ that is NOT 'default'
            su_url_pattern = re.compile(r'(/uploads/)(?!default/)([^/]+)(/.*?)(["\'])')
            su_replacement = r"\1default\3\4"  # Replace with: /uploads/default/img.jpg"

        total_objects_fixed = 0
        total_journals_fixed = 0

        try:
            all_users = db.query(DbUser).all()
            print(f"Found {len(all_users)} user(s) to check...")

            for user in all_users:
                print(f"--- Processing user: {user.username} ---")

                # 1. Fix AstroObject Notes (Private and Shared)
                objects_to_fix = db.query(AstroObject).filter_by(user_id=user.id).all()
                objects_fixed_count = 0
                for obj in objects_to_fix:
                    fixed = False

                    # --- Fix Private Notes ---
                    notes = obj.project_name
                    if notes and "/uploads/" in notes:
                        # Step 1: Fix absolute URLs
                        new_notes, count_abs = abs_url_pattern.subn(
                            abs_replacement, notes
                        )
                        # Step 2: Fix user paths (if in SU mode and pattern exists)
                        count_su = 0
                        if su_url_pattern and "/uploads/" in new_notes:
                            new_notes, count_su = su_url_pattern.subn(
                                su_replacement, new_notes
                            )

                        if count_abs > 0 or count_su > 0:
                            obj.project_name = new_notes
                            fixed = True

                    # --- Fix Shared Notes ---
                    shared_notes = obj.shared_notes
                    if shared_notes and "/uploads/" in shared_notes:
                        # Step 1: Fix absolute URLs
                        new_shared_notes, count_abs = abs_url_pattern.subn(
                            abs_replacement, shared_notes
                        )
                        # Step 2: Fix user paths (if in SU mode)
                        count_su = 0
                        if su_url_pattern and "/uploads/" in new_shared_notes:
                            new_shared_notes, count_su = su_url_pattern.subn(
                                su_replacement, new_shared_notes
                            )

                        if count_abs > 0 or count_su > 0:
                            obj.shared_notes = new_shared_notes
                            fixed = True

                    if fixed:
                        objects_fixed_count += 1

                if objects_fixed_count > 0:
                    print(
                        f"    Fixed links in {objects_fixed_count} AstroObject note(s)."
                    )
                    total_objects_fixed += objects_fixed_count

                # 2. Fix JournalSession Notes
                sessions_to_fix = (
                    db.query(JournalSession).filter_by(user_id=user.id).all()
                )
                sessions_fixed_count = 0
                for session in sessions_to_fix:
                    notes = session.notes
                    if notes and "/uploads/" in notes:
                        # Step 1: Fix absolute URLs
                        new_journal_notes, count_abs = abs_url_pattern.subn(
                            abs_replacement, notes
                        )
                        # Step 2: Fix user paths (if in SU mode)
                        count_su = 0
                        if su_url_pattern and "/uploads/" in new_journal_notes:
                            new_journal_notes, count_su = su_url_pattern.subn(
                                su_replacement, new_journal_notes
                            )

                        if count_abs > 0 or count_su > 0:
                            session.notes = new_journal_notes
                            sessions_fixed_count += 1

                if sessions_fixed_count > 0:
                    print(
                        f"    Fixed links in {sessions_fixed_count} JournalSession note(s)."
                    )
                    total_journals_fixed += sessions_fixed_count

                if objects_fixed_count == 0 and sessions_fixed_count == 0:
                    print("    No broken image links found for this user.")

            # Commit all changes for all users at the end
            db.commit()
            print("--- [REPAIR COMPLETE] ---")
            print(
                f"✅ Repaired links in {total_objects_fixed} object notes and {total_journals_fixed} journal notes."
            )
            print("Database has been updated with relative image paths.")

        except Exception as e:
            db.rollback()
            print(f"❌ FATAL ERROR: {e}")
            print("Database has been rolled back. No changes were saved.")
            traceback.print_exc()
        finally:
            db.close()

    @app.cli.command("repair-corrupt-ids")
    def repair_corrupt_ids_command():
        """
        Finds and repairs object IDs that were corrupted by the old
        over-aggressive normalization script (e.g., 'SH2129' -> 'SH 2-129').
        This script is RULE-BASED and fixes all matching corrupt patterns.
        It runs IN-PLACE on the database to fix the names
        and re-link all associated journal entries.
        """
        print("--- [EMERGENCY OBJECT ID REPAIR SCRIPT] ---")
        db = get_db()

        # --- THIS LIST IS NOW FIXED TO MATCH normalize_object_name ---
        repair_rules = [
            # IC 405 -> IC405
            (re.compile(r"^(IC)(\d+)$"), r"IC \2"),
            # SNR G180.0-01.7 -> SNRG180.001.7
            (
                re.compile(r"^(SNRG)(\d+\.\d+?)(\d+\.\d+)$"),
                r"SNR G\2-\3",
            ),  # (non-greedy)
            # LHA 120-N 70 -> LHA120N70
            (
                re.compile(r"^(LHA)(\d+)(N)(\d+)$"),
                r"LHA \2-\3 \4",
            ),  # (FIXED regex and replacement)
            # SH 2-129 -> SH2129
            (re.compile(r"^(SH2)(\d+)$"), r"SH 2-\2"),
            # TGU H1867 -> TGUH1867
            (re.compile(r"^(TGUH)(\d+)$"), r"TGU H\2"),
            # VDB 1 -> VDB1
            (re.compile(r"^(VDB)(\d+)$"), r"VDB \2"),
            # NGC 1976 -> NGC1976
            (re.compile(r"^(NGC)(\d+)$"), r"NGC \2"),
            # IC 1805 -> IC1805
            (re.compile(r"^(IC)(\d+)$"), r"IC \2"),
            # GUM 16 -> GUM16
            (re.compile(r"^(GUM)(\d+)$"), r"GUM \2"),
            # CTA 1 -> CTA1
            (re.compile(r"^(CTA)(\d+)$"), r"CTA \2"),
            # HB 3 -> HB3
            (re.compile(r"^(HB)(\d+)$"), r"HB \2"),
            # PN ARO 121 -> PNARO121
            (re.compile(r"^(PNARO)(\d+)$"), r"PN ARO \2"),
            # LIESTO 1 -> LIESTO1
            (re.compile(r"^(LIESTO)(\d+)$"), r"LIESTO \2"),
            # PK 081-14.1 -> PK08114.1
            (re.compile(r"^(PK)(\d+)(\d{2}\.\d+)$"), r"PK \2-\3"),
            # PN G093.3-02.4 -> PNG093.302.4
            (re.compile(r"^(PNG)(\d+\.\d+?)(\d+\.\d+)$"), r"PN G\2-\3"),  # (non-greedy)
            # WR 134 -> WR134
            (re.compile(r"^(WR)(\d+)$"), r"WR \2"),
            # ABELL 21 -> ABELL21
            (re.compile(r"^(ABELL)(\d+)$"), r"ABELL \2"),
            # BARNARD 33 -> BARNARD33
            (re.compile(r"^(BARNARD)(\d+)$"), r"BARNARD \2"),
        ]

        try:
            all_users = db.query(DbUser).all()
            print(f"Found {len(all_users)} users to check...")
            total_repaired = 0

            for user in all_users:
                print(f"--- Processing user: {user.username} ---")

                # Get all objects for this user
                user_objects = db.query(AstroObject).filter_by(user_id=user.id).all()

                # Create a lookup of objects by their name for collision detection
                objects_by_name = {obj.object_name: obj for obj in user_objects}

                repaired_in_this_user = 0

                # Iterate over a copy of the list, as we may be modifying objects
                for obj_to_fix in list(user_objects):
                    corrupt_name = obj_to_fix.object_name
                    repaired_name = None

                    # Apply rules to find a match
                    for pattern, replacement in repair_rules:
                        if pattern.match(corrupt_name):
                            repaired_name = pattern.sub(replacement, corrupt_name)
                            break  # Stop on the first rule that matches

                    # If we found a repair and it's different, apply it
                    if repaired_name and repaired_name != corrupt_name:
                        # Check if the "repaired" name *already* exists (collision)
                        existing_correct_obj = objects_by_name.get(repaired_name)

                        if (
                            existing_correct_obj
                            and existing_correct_obj.id != obj_to_fix.id
                        ):
                            # --- MERGE PATH ---
                            print(
                                f"    WARNING: Found '{corrupt_name}' and '{repaired_name}'. Merging corrupt into correct..."
                            )

                            # 1. Merge notes
                            if obj_to_fix.project_name:
                                notes_to_merge = obj_to_fix.project_name or ""
                                if not (
                                    not notes_to_merge
                                    or notes_to_merge.lower().strip()
                                    in ("none", "<div>none</div>", "null")
                                ):
                                    existing_correct_obj.project_name = (
                                        (existing_correct_obj.project_name or "")
                                        + f"<br>---<br><em>(Merged from corrupt: {corrupt_name})</em><br>{notes_to_merge}"
                                    )

                            # 2. Re-link journals that point to the corrupt name
                            db.query(JournalSession).filter_by(
                                user_id=user.id, object_name=corrupt_name
                            ).update({"object_name": repaired_name})

                            # 3. Delete the corrupt object
                            db.delete(obj_to_fix)
                            print(f"      -> Merged and deleted '{corrupt_name}'.")

                        else:
                            # --- RENAME PATH ---
                            print(
                                f"    Repairing: '{corrupt_name}' -> '{repaired_name}'"
                            )

                            # 1. Rename the object
                            obj_to_fix.object_name = repaired_name

                            # 2. Update all journal entries that pointed to the corrupt name
                            db.query(JournalSession).filter_by(
                                user_id=user.id, object_name=corrupt_name
                            ).update({"object_name": repaired_name})

                            # 3. Update the lookup map for this user
                            objects_by_name[repaired_name] = obj_to_fix
                            if corrupt_name in objects_by_name:
                                del objects_by_name[corrupt_name]

                        total_repaired += 1
                        repaired_in_this_user += 1

                if repaired_in_this_user > 0:
                    print(f"  Repaired {repaired_in_this_user} objects for this user.")
                else:
                    print(
                        "  No corrupt IDs matching the repair rules were found for this user."
                    )

            # Commit all changes for all users at the very end
            db.commit()
            print("--- [REPAIR COMPLETE] ---")
            print(
                f"✅ Repaired and re-linked {total_repaired} objects across all users."
            )
            print("Database corruption has been fixed.")

        except Exception as e:
            db.rollback()
            print(f"❌ FATAL ERROR: {e}")
            print("Database has been rolled back. No changes were saved.")
            traceback.print_exc()
        finally:
            db.close()

    @app.cli.command("seed-guest-account")
    def seed_guest_account_command():
        """
        Safely adds default rigs AND journal entries to the 'guest_user' account.
        This is for live systems to populate the demo account.
        V2: Cleans up guest account first and reads from TEMPLATE_DIR.
        """
        print("--- [SEEDING GUEST ACCOUNT (v2 - FIX)] ---")
        db = get_db()
        try:
            # 1. Find the guest_user
            guest_user = db.query(DbUser).filter_by(username="guest_user").one_or_none()
            if not guest_user:
                print("ERROR: 'guest_user' account not found. Cannot seed.")
                return
            print(f"Found 'guest_user' (ID: {guest_user.id}).")

            # 2. --- CLEAN UP FIRST ---
            # This is critical to remove your personal data from the guest account.
            print("Cleaning up any existing data from guest_user account...")
            db.query(Rig).filter_by(user_id=guest_user.id).delete()
            db.query(Component).filter_by(user_id=guest_user.id).delete()
            db.query(JournalSession).filter_by(user_id=guest_user.id).delete()
            db.commit()  # Commit the deletions
            print("...Cleanup complete.")

            # 3. --- Seed Rigs from TEMPLATES ---
            # Use TEMPLATE_DIR (config_templates), not CONFIG_DIR (instance/configs)
            rigs_template_path = os.path.join(TEMPLATE_DIR, "rigs_default.yaml")
            if os.path.exists(rigs_template_path):
                rigs_yaml, error = _read_yaml(rigs_template_path)
                if error:
                    print(
                        f"ERROR (Rigs): Could not read 'config_templates/rigs_default.yaml': {error}"
                    )
                elif rigs_yaml is not None:
                    print("Migrating components and rigs from template...")
                    _migrate_components_and_rigs(
                        db, guest_user, rigs_yaml, "guest_user"
                    )
                    print("...Rigs seeded.")
            else:
                print("WARNING: 'config_templates/rigs_default.yaml' not found.")

            # 4. --- Seed Journal from TEMPLATES ---
            # Use TEMPLATE_DIR, not CONFIG_DIR
            journal_template_path = os.path.join(TEMPLATE_DIR, "journal_default.yaml")
            if os.path.exists(journal_template_path):
                journal_yaml, error = _read_yaml(journal_template_path)
                if error:
                    print(
                        f"ERROR (Journal): Could not read 'config_templates/journal_default.yaml': {error}"
                    )
                elif journal_yaml is not None:
                    print("Migrating journal entries from template...")
                    _migrate_journal(db, guest_user, journal_yaml)
                    print("...Journal seeded.")
            else:
                print("WARNING: 'config_templates/journal_default.yaml' not found.")

            db.commit()  # Commit the additions
            print("--- [SEEDING COMPLETE] ---")
            print(
                "✅ Successfully cleaned and populated the 'guest_user' account with demo data."
            )

        except Exception as e:
            db.rollback()
            print(f"❌ FATAL ERROR: {e}")
            print("Database has been rolled back. No changes were saved.")
            traceback.print_exc()
        finally:
            db.close()

    @app.cli.command("reset-guest-from-template")
    def reset_guest_from_template_command():
        """
        COMPLETELY WIPES the 'guest_user' and re-seeds it strictly from the
        source code's 'config_templates' directory.
        Use this to force the guest view to match the shipped defaults.
        """
        print("--- [RESETTING GUEST FROM TEMPLATES] ---")
        db = get_db()
        try:
            # 1. Ensure guest_user exists
            guest_user = db.query(DbUser).filter_by(username="guest_user").one_or_none()
            if not guest_user:
                guest_user = DbUser(username="guest_user", active=True)
                db.add(guest_user)
                db.flush()
                print(f"Created 'guest_user' (ID: {guest_user.id}).")
            else:
                print(f"Found 'guest_user' (ID: {guest_user.id}).")

            # 2. WIPE ALL DATA
            print("Wiping all existing data for guest_user...")
            # Order matters for foreign keys
            db.query(JournalSession).filter_by(user_id=guest_user.id).delete()
            db.query(Project).filter_by(user_id=guest_user.id).delete()
            db.query(SavedFraming).filter_by(user_id=guest_user.id).delete()
            db.query(SavedView).filter_by(user_id=guest_user.id).delete()
            db.query(Rig).filter_by(user_id=guest_user.id).delete()
            db.query(Component).filter_by(user_id=guest_user.id).delete()
            db.query(AstroObject).filter_by(user_id=guest_user.id).delete()
            db.query(Location).filter_by(user_id=guest_user.id).delete()
            db.query(UiPref).filter_by(user_id=guest_user.id).delete()
            db.flush()
            print("...Wipe complete.")

            # 3. LOAD TEMPLATES
            # We look for 'config_guest_user.yaml' first, fall back to 'config_default.yaml'
            cfg_path = os.path.join(TEMPLATE_DIR, "config_guest_user.yaml")
            if not os.path.exists(cfg_path):
                cfg_path = os.path.join(TEMPLATE_DIR, "config_default.yaml")

            rigs_path = os.path.join(TEMPLATE_DIR, "rigs_default.yaml")
            jrn_path = os.path.join(TEMPLATE_DIR, "journal_default.yaml")

            print(f"Loading Config from: {os.path.basename(cfg_path)}")
            cfg_data, _ = _read_yaml(cfg_path)

            print(f"Loading Rigs from: {os.path.basename(rigs_path)}")
            rigs_data, _ = _read_yaml(rigs_path)

            print(f"Loading Journal from: {os.path.basename(jrn_path)}")
            jrn_data, _ = _read_yaml(jrn_path)

            # 4. RE-SEED
            if cfg_data:
                print("Seeding Locations, Objects, Prefs...")
                _migrate_locations(db, guest_user, cfg_data)
                _migrate_objects(db, guest_user, cfg_data)
                _migrate_ui_prefs(db, guest_user, cfg_data)
                _migrate_saved_framings(db, guest_user, cfg_data)
                _migrate_saved_views(db, guest_user, cfg_data)

            if rigs_data:
                print("Seeding Rigs...")
                _migrate_components_and_rigs(db, guest_user, rigs_data, "guest_user")

            if jrn_data:
                print("Seeding Journal...")
                _migrate_journal(db, guest_user, jrn_data)

            db.commit()
            print("✅ Guest user fully reset to template defaults.")

        except Exception as e:
            db.rollback()
            print(f"❌ FATAL ERROR: {e}")
            traceback.print_exc()
