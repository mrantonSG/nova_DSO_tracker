import re
import os

# Configuration
INPUT_FILE = "README.md"
OUTPUT_FILE = "README_DOCKER.md"
# Points to the raw file location on GitHub
REPO_ROOT = "https://raw.githubusercontent.com/mrantonSG/nova_DSO_tracker/master/"


def convert_links():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found.")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    # Regex to find markdown images: ![alt](path)
    pattern = re.compile(r'!\[(.*?)\]\((.*?)\)')

    def replace_path(match):
        alt_text = match.group(1)
        path = match.group(2)

        # If it is already an absolute URL (http/https), ignore it
        if path.startswith("http"):
            return match.group(0)

        # Strip leading ./ if present
        clean_path = path.lstrip("./")

        # Create absolute GitHub raw URL
        return f'![{alt_text}]({REPO_ROOT}{clean_path})'

    new_content = pattern.sub(replace_path, content)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(new_content)

    print(f"Successfully generated {OUTPUT_FILE}")


if __name__ == "__main__":
    convert_links()