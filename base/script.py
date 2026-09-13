from pathlib import Path

# Directory containing this script
script_dir = Path(__file__).resolve().parent

# One directory above the script
parent_dir = script_dir.parent

# List folders
for folder in parent_dir.iterdir():
    if folder.is_dir():
        print(folder.name)