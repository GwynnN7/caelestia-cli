import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from caelestia.utils.paths import cache_dir, config_dir, data_dir, dots_dir, state_dir

# Dirs to never prune even if empty
_PROTECTED_DIRS = frozenset({Path.home(), config_dir, data_dir, state_dir, cache_dir})


def needs_sudo(path: Path) -> bool:
    """Whether creating, replacing or removing `path` requires root.

    Used for paths the manifest no longer describes, where the entry's `sudo`
    flag is no longer available to consult.
    """

    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return not os.access(parent, os.W_OK)


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
            # `install` sets the owner and mode explicitly: `cp` keeps whatever the
            # existing dest had, so an upstream mode change (e.g. a script becoming
            # executable) would never reach the deployed file. Writing a temp file
            # first and renaming it keeps the replacement atomic, so an interrupted
            # deploy can't leave a half written /etc/sudoers.d entry behind.
            tmp = dest.parent / f".{dest.name}.caelestia-new"
            mode = f"{src.stat().st_mode & 0o777:o}"
            subprocess.run(
                ["sudo", "install", "-D", "-o", "root", "-g", "root", "-m", mode, str(src), str(tmp)], check=True
            )
            try:
                subprocess.run(["sudo", "mv", "-f", str(tmp), str(dest)], check=True)
            except BaseException:
                subprocess.run(["sudo", "rm", "-f", str(tmp)], check=False)
                raise
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
