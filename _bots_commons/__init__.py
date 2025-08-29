from pathlib import Path

MAX_OUTPUT_LENGTH = 65000  # GitHub API limit for check-run output.text

LOCAL_ROOT_DIR = Path(__file__).parent.parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"
