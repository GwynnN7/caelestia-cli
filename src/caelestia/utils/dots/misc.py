import os
import subprocess

from caelestia.utils.dots.manifest import Manifest
from caelestia.utils.dots.packages import PackageInstaller
from caelestia.utils.dots.source import DotsSource
from caelestia.utils.io import info, log, warn
from caelestia.utils.paths import dots_dir


def build_local_packages(installer: PackageInstaller, source: DotsSource, paths: list[str]) -> dict[str, list[str]]:
    """Build and install each local PKGBUILD dir, returning {path: installed package names}."""

    built: dict[str, list[str]] = {}
    for path in paths:
        directory = source.working_path(path)
        if not directory.is_dir():
            warn(f"missing in repo, skipping: {path}")
            continue
        log(f"Building {path}...")
        built[path] = installer.build_install(directory)
    return built

def build_manual_packages(installer: PackageInstaller, packages: list) -> None:
    """Build and install each manual git package from the manifest."""
    if not packages:
        return
        
    print()
    log("Building manual packages...")
    for pkg in packages:
        try:
            installer.build_manual_package(pkg.name, pkg.repo, pkg.build_cmds, pkg.post_install)
            info(f"Successfully built and installed {pkg.name}")
        except Exception as e:
            warn(f"Failed to build manual package {pkg.name}: {e}")

def run_hooks(manifest: Manifest, kind: str) -> None:
    """Run the global + enabled components' hooks of the given kind (e.g. "post_install")."""

    hooks = manifest.enabled_hooks(kind)
    if not hooks:
        return

    print()
    log(f"Running {kind.replace('_', '-')} hooks...")
    env = {**os.environ, "CAELESTIA_DOTS": str(dots_dir)}
    for hook in hooks:
        info(f"Running hook: {hook}")
        result = subprocess.run(hook, shell=True, env=env)
        if result.returncode != 0:
            warn(f"hook exited with {result.returncode}")
