# modules/updater.py

import os
import shutil
import subprocess
import sys


def copy_directory(src, dst, skip_files=None):
    """
    Recursively copy directory from src to dst.
    If skip_files is provided (a list of filenames), skip those files.
    """
    if skip_files is None:
        skip_files = []
    if not os.path.exists(dst):
        os.makedirs(dst)
    for item in os.listdir(src):
        s_item = os.path.join(src, item)
        d_item = os.path.join(dst, item)
        if os.path.isfile(s_item):
            if item in skip_files:
                # Skip files that are in skip_files.
                continue
            shutil.copy2(s_item, d_item)
        elif os.path.isdir(s_item):
            copy_directory(s_item, d_item, skip_files)


def main():
    print("Starting Nova DSO Tracker updater...")

    # Get the project root (the parent directory of the modules folder).
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo_url = "https://github.com/mrantonSG/nova_DSO_tracker.git"
    temp_dir = os.path.join(base_dir, "_update_temp")

    # Clone repo into temporary directory.
    print("Cloning the latest version into a temporary directory...")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    subprocess.run(["git", "clone", repo_url, temp_dir], check=True)

    # Define a list of items to copy from the repository.
    # We include the main script, templates, README, and the modules folder.
    items_to_copy = ["nova.py", "templates", "README.md", "modules"]

    for item in items_to_copy:
        src_path = os.path.join(temp_dir, item)
        dst_path = os.path.join(base_dir, item)

        # Remove destination if it exists.
        if os.path.exists(dst_path):
            if os.path.isdir(dst_path):
                shutil.rmtree(dst_path)
            else:
                os.remove(dst_path)

        if os.path.isdir(src_path):
            if item == "modules":
                # For the modules folder, copy everything except updater.py.
                copy_directory(src_path, dst_path, skip_files=["updater.py"])
            else:
                shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)
        print(f"✔ Updated {item}")

    # Clean up temporary folder.
    shutil.rmtree(temp_dir)
    print("Temporary files cleaned up.")
    print("Update complete. Restarting Nova DSO Tracker...")

    nova_script = os.path.join(base_dir, "nova.py")

    # Set environment variable to disable debug and reloader during restart.
    os.environ["NOVA_NO_DEBUG"] = "1"

    # Replace current process with the new application.
    os.execv(sys.executable, [sys.executable, nova_script])


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"❌ Update failed: {e}")
        sys.exit(1)