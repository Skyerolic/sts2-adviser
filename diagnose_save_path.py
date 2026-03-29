"""
Diagnostic script: Find STS2 save file location

Run this to find where save files are stored on your system.
"""

import os
import sys
import io
from pathlib import Path
import json

# Force UTF-8 output on Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

def find_sts2_files():
    """Search for STS2 save file locations"""

    print("=" * 70)
    print("STS2 Save File Location Diagnostic")
    print("=" * 70)

    # Common search locations
    search_locations = [
        # Windows AppData
        Path.home() / "AppData" / "Local" / "SlayTheSpire2",
        Path.home() / "AppData" / "Roaming" / "SlayTheSpire2",

        # Steam save location
        Path.home() / "AppData" / "Roaming" / "SlayTheSpire2" / "steam",

        # Other possible locations
        Path("C:/Program Files/Steam/steamapps/common/SlayTheSpire2"),
        Path("C:/Program Files (x86)/Steam/steamapps/common/SlayTheSpire2"),

        # User-specified location
        Path(os.environ.get("STS2_PATH", "")),
    ]

    print("\n[1] Searching for save files and logs...\n")

    found_saves = {}
    found_logs = {}

    for location in search_locations:
        if not location.exists():
            continue

        print(f"[DIR] {location}")

        # Search for .save files
        save_files = list(location.rglob("*.save"))
        if save_files:
            found_saves[str(location)] = save_files
            for save_file in save_files:
                print(f"      [SAVE] {save_file.relative_to(location)}")

        # Search for .log files
        log_files = list(location.rglob("*.log"))
        if log_files:
            found_logs[str(location)] = log_files
            for log_file in log_files[:3]:  # Show first 3
                print(f"      [LOG]  {log_file.relative_to(location)}")

    # Generate diagnostic results
    print("\n" + "=" * 70)
    print("[2] Diagnostic Results\n")

    if found_saves:
        print(f"FOUND: {len(found_saves)} location(s) with .save files:")
        for path, files in found_saves.items():
            print(f"\n   Location: {path}")
            for f in files:
                size = f.stat().st_size / 1024  # KB
                print(f"     - {f.name} ({size:.1f} KB)")

        # Try to read latest save
        all_saves = []
        for files in found_saves.values():
            all_saves.extend(files)

        if all_saves:
            latest_save = max(all_saves, key=lambda f: f.stat().st_mtime)
            print(f"\n   Latest save: {latest_save}")

            # Try to read save contents
            try:
                with open(latest_save, 'r', encoding='utf-8') as f:
                    save_data = json.load(f)
                    if 'players' in save_data and save_data['players']:
                        player = save_data['players'][0]
                        char = player.get('character_id', 'unknown')
                        floor = save_data.get('current_act_index', 0) + 1
                        hp = player.get('current_hp', 0)
                        max_hp = player.get('max_hp', 1)
                        print(f"\n   Game State:")
                        print(f"     Character: {char}")
                        print(f"     Floor: F{floor}")
                        print(f"     HP: {hp}/{max_hp}")
            except Exception as e:
                print(f"\n   WARNING: Could not read save: {e}")
    else:
        print("ERROR: No .save files found")

    if found_logs:
        print(f"\n\nFOUND: {len(found_logs)} location(s) with log files:")
        for path, files in found_logs.items():
            print(f"\n   Location: {path}")
            for f in files[:3]:
                print(f"     - {f.name}")

    else:
        print("\n\nERROR: No log files (.log) found")

    # Suggestions
    print("\n" + "=" * 70)
    print("[3] Recommended Actions\n")

    if found_saves:
        save_path = list(found_saves.keys())[0]
        print(f"Set Save Path in UI Settings:")
        print(f"  {save_path}")
    else:
        print("ERROR: Save file not found")
        print("   Required:")
        print("   1. Launch game at least once")
        print("   2. Check STS2 installation in Steam")
        print("   3. Set STS2_PATH environment variable")

    if found_logs:
        log_path = list(found_logs.keys())[0]
        print(f"\nSet Log Path in UI Settings:")
        print(f"  {log_path}")
    else:
        print("\nNOTE: Log files may appear after launching the game")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    find_sts2_files()
