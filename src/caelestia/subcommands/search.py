import json
import subprocess
import time
from argparse import Namespace
from pathlib import Path

SEARCH_PNG = Path("/tmp/caelestia-search.png")
SEARCH_DONE = Path("/tmp/caelestia-search.done")


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        SEARCH_PNG.unlink(missing_ok=True)
        SEARCH_DONE.unlink(missing_ok=True)

        subprocess.Popen(
            ["qs", "-c", "caelestia", "ipc", "call", "picker", "openSearch"],
            start_new_session=True,
        )

        for _ in range(100):
            if SEARCH_DONE.exists():
                break
            time.sleep(0.05)
        else:
            return

        SEARCH_DONE.unlink(missing_ok=True)

        response = subprocess.check_output(
            [
                "curl", "-sSf", "--connect-timeout", "5", "--max-time", "15",
                "-F", f"files[]=@{SEARCH_PNG}",
                "https://uguu.se/upload",
            ],
            text=True,
        )
        url = json.loads(response).get("files", [{}])[0].get("url", "")
        if url:
            subprocess.Popen(
                ["xdg-open", f"https://lens.google.com/uploadbyurl?url={url}"],
                start_new_session=True,
            )
