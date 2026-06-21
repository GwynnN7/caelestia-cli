import shutil
import subprocess
import tempfile
from pathlib import Path

from caelestia.utils.paths import cache_dir, config_dir, data_dir, dots_dir, state_dir

# Dirs to never prune even if empty
_PROTECTED_DIRS = frozenset({Path.home(), config_dir, data_dir, state_dir, cache_dir})


class Deployer:
    """Places files from the dots clone into their destinations."""

    def __init__(self):
        self.deployed_files: dict[str, str] = {}

    def place(self, src: Path, dest: Path, sudo: bool = False) -> None:
        """Place a whole entry (file or directory tree), replacing any existing dest."""

        if src.is_dir():
            self.place_dir(src, dest, sudo=sudo)
        else:
            self.place_file(src, dest, sudo=sudo)

    def place_dir(self, src: Path, dest: Path, sudo: bool = False) -> None:
        if dest.is_symlink() or dest.is_file():
            self.remove(dest, sudo=sudo)

        if sudo:
            subprocess.run(["sudo", "mkdir", "-p", str(dest)], check=True)
            
        for path in src.rglob("*"):
            if path.is_file():
                self.place_file(path, dest / path.relative_to(src), sudo=sudo)
            elif path.is_dir():
                target = dest / path.relative_to(src)
                if sudo:
                    subprocess.run(["sudo", "mkdir", "-p", str(target)], check=True)
                else:
                    target.mkdir(parents=True, exist_ok=True)

    def place_file(self, src: Path, dest: Path, record: bool = True, sudo: bool = False) -> None:
        """Atomically place a single file, replacing any existing dest."""

        if dest.is_dir() and not dest.is_symlink():
            self.remove(dest, sudo=sudo)

        if sudo:
            subprocess.run(["sudo", "mkdir", "-p", str(dest.parent)], check=True)
            subprocess.run(["sudo", "cp", str(src), str(dest)], check=True)
            
            # Optional: Ensure root owns the system configs
            #subprocess.run(["sudo", "chown", "root:root", str(dest)], check=True)
        else:
            # Existing standard user deployment
            dest.parent.mkdir(parents=True, exist_ok=True)
            f = tempfile.NamedTemporaryFile(dir=dest.parent, delete=False)
            f.close()
            try:
                shutil.copyfile(src, f.name)
                shutil.copymode(src, f.name)
                Path(f.name).replace(dest)
            except BaseException:
                Path(f.name).unlink()
                raise

        if record:
            self.deployed_files[str(dest)] = str(src.relative_to(dots_dir))

    def write_new(self, src: Path, dest: Path, sudo: bool = False) -> Path:
        """Write the upstream version alongside dest as <dest>.new and return that path."""

        new_path = dest.parent / f"{dest.name}.new"
        self.place_file(src, new_path, record=False, sudo=sudo)
        return new_path

    def remove(self, path: Path, sudo: bool = False) -> None:
        if sudo:
            subprocess.run(["sudo", "rm", "-rf", str(path)], check=True)
        elif path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def prune_empty_dirs(self, start: Path, stop: Path) -> None:
        """Removes dirs recursively from start to stop.

        Will never prune protected dirs (home, config, cache, etc).
        """

        parent = start.parent
        while parent != stop and stop in parent.parents and parent not in _PROTECTED_DIRS:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
