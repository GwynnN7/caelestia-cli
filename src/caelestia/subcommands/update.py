import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from caelestia.utils.dots.deployer import Deployer
from caelestia.utils.dots.diff import Changeset
from caelestia.utils.dots.manifest import ComponentError, Manifest, ManifestError
from caelestia.utils.dots.misc import build_local_packages, run_hooks
from caelestia.utils.dots.packages import PackageError, PackageInstaller
from caelestia.utils.dots.source import DotsSource, SourceError
from caelestia.utils.dots.state import DotsState
from caelestia.utils.io import disable_input, fatal, info, log, prompt_selection, warn

from caelestia.utils.shell import detect_shell_management_source, shell_package_matching_cli


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.noconfirm:
            disable_input()

        state = DotsState.load()
        if state.applied_rev is None:
            fatal("dots not installed yet. Run `caelestia install` first.")

        # Captured before the manifest resolves, to tell apart components that are
        # already set up from ones being enabled for the first time
        previously_enabled = list(state.enabled_components)

        # Run system update
        try:
            installer = PackageInstaller.get(self.args.aur_helper or state.aur_helper, self.args.noconfirm)
            installer.system_update()
        except PackageError as e:
            fatal(e)

        # Get manifest or exit if up to date
        source, tip, manifest = self.fetch_manifest(state, state.applied_rev)

        # Apply file changes
        entries = manifest.enabled_entries()

        if getattr(self.args, "force_dotfiles", False):
            print()
            log("Force re-installing all configs...")

            # The working tree still sits at the applied rev, so without this the
            # forced deploy would re-place the *old* files under the new rev
            try:
                source.checkout_tip()
            except SourceError as e:
                fatal(e)

            deployer = Deployer()
            for entry in entries:
                src = source.working_path(entry.expanded_src())
                if not src.exists():
                    warn(f"missing in source, skipping: {entry.src}")
                    continue
                dests = entry.expanded_dests()
                if not dests:
                    warn(f"dest glob matched nothing, skipping: {entry.dest}")
                    continue
                for dest in dests:
                    deployer.place(src, Path(dest), sudo=entry.sudo)
                    info(f"{entry.src} -> {dest}")
            changeset = Changeset()
            new_files, revived_files = [], []
            placed = deployer.deployed_files
        else:
            try:
                changeset = Changeset.compute(source, state.applied_rev, tip, entries, state.deployed_files)
                source.checkout_tip()
            except SourceError as e:
                fatal(e)
            new_files, revived_files, placed = self.deploy_changeset(source, changeset)

        # Persist file changes immediately so a later failure can't lose track of them
        deployed = dict(state.deployed_files)
        for dest in (*changeset.deletes, *changeset.stale, *changeset.untracked):
            deployed.pop(str(dest), None)
        for repofile, dest in changeset.remap:
            deployed[str(dest)] = repofile
        deployed.update(placed)
        state.deployed_files = deployed
        state.save()

        # Install new/remove old packages
        desired = manifest.enabled_packages()
        desired_local = manifest.enabled_local_packages()
        try:
            state.packages = self.sync_packages(installer, state.packages, desired)
            state.save()
            state.local_packages = self.sync_local_packages(installer, source, state.local_packages, desired_local)
            state.save()
        except PackageError as e:
            fatal(e)

        # Run hooks
        new_components = [name for name in manifest.enabled_components if name not in previously_enabled]
        if new_components:
            info(f"Newly enabled components: {', '.join(new_components)}")
            run_hooks(manifest, "post_install", new_components)
        run_hooks(manifest, "post_update", [n for n in manifest.enabled_components if n not in new_components])

        # Update shell with optional pkgit support
        _update_shell_with_pkgit(installer, self.args.noconfirm)

        # Mark the new revision applied
        state.applied_rev = tip
        state.enabled_components = manifest.enabled_components
        state.aur_helper = getattr(installer, "helper", state.aur_helper)
        state.save()

        self.summarize(changeset, new_files, revived_files)

    def fetch_manifest(self, state: DotsState, applied_rev: str) -> tuple[DotsSource, str, Manifest]:
        print()
        log("Fetching dots repo...")
        source = DotsSource()
        try:
            source.ensure()
            tip = source.tip_rev()
            
            if tip == applied_rev and getattr(self.args, "git", False):
                info("Dots already up to date.")
                sys.exit(0)
            elif tip == applied_rev and not getattr(self.args, "git", False):
                info("Dots are at the latest commit, but forcing update...")

            manifest = source.manifest_at(tip)
            if source.has_rev(applied_rev):
                known = set(source.manifest_at(applied_rev).components)
            else:
                # Treat all components as known if rev is invalid so we don't overwrite existing prefs
                known = set(manifest.components)
        except (SourceError, ManifestError) as e:
            fatal(e)

        # Enable components recorded at install time + any new components that are default on
        enabled = [
            name
            for name, comp in manifest.components.items()
            if name in state.enabled_components or (name not in known and comp.default)
        ]

        # Let the user opt into any new optional components
        new_comps = [name for name, comp in manifest.components.items() if name not in known and not comp.default]
        if new_comps:
            info(f"New components: {', '.join(new_comps)}")
            enabled += prompt_selection(new_comps, "Components to enable?")

        disabled = [name for name in manifest.components if name not in enabled]
        try:
            manifest.resolve_components(enable=enabled, disable=disabled)
        except ComponentError as e:
            fatal(e)

        info(f"Enabled components: {', '.join(enabled) or 'none'}")

        return source, tip, manifest

    def deploy_changeset(
        self, source: DotsSource, changeset: Changeset
    ) -> tuple[list[Path], list[Path], dict[str, str]]:
        print()

        if changeset.is_empty():
            info("No configs to update.")
            return [], [], {}

        log("Updating configs...")
        deployer = Deployer()

        for repofile, dest in changeset.place:
            src = source.working_path(repofile)
            if not src.exists():
                warn(f"missing in source, skipping: {repofile}")
                continue
            deployer.place_file(src, dest, sudo=(dest in changeset.sudo_files))
            info(f"{repofile} -> {dest}")

        new_files = []
        for repofile, dest in changeset.conflicts:
            src = source.working_path(repofile)
            if not src.exists():
                warn(f"missing in source, skipping: {repofile}")
                continue
            new_path = deployer.write_new(src, dest, sudo=(dest in changeset.sudo_files))
            new_files.append(new_path)
            warn(f"{dest} has local changes; upstream version written as {new_path.name}")

        revived_files = []
        for repofile, dest in changeset.deleted_changed:
            src = source.working_path(repofile)
            if not src.exists():
                warn(f"missing in source, skipping: {repofile}")
                continue
            new_path = deployer.write_new(src, dest, sudo=(dest in changeset.sudo_files))
            revived_files.append(new_path)
            warn(f"{dest} was removed but changed upstream; upstream version written as {new_path.name}")

        failed = []
        for dest in changeset.deletes:
            try:
                deployer.remove(dest, sudo=(dest in changeset.sudo_files))
            except (OSError, subprocess.CalledProcessError) as e:
                # Not worth aborting a whole update (and losing track of everything
                # already placed) over one file that won't budge
                warn(f"failed to remove {dest}: {e}")
                failed.append(dest)
                continue
            deployer.prune_empty_dirs(dest, Path.home())
            info(f"Removed {dest}")

        # Leave failures in the deployed state so the next update retries them
        for dest in failed:
            changeset.deletes.remove(dest)

        return new_files, revived_files, deployer.deployed_files

    def sync_packages(self, installer: PackageInstaller, current: dict[str, str], desired: list[str]) -> dict[str, str]:
        if not getattr(self.args, "git", False):
            to_install = desired
        else:
            to_install = [p for p in desired if p not in current]

        to_remove = [p for p in current if p not in desired]
        installed = dict(current)

        if to_install:
            print()
            info(f"Installing/Verifying packages: {', '.join(to_install)}")
            installed.update(zip(to_install, installer.install(to_install)))

        if to_remove:
            print()
            info(f"Packages no longer required: {', '.join(to_remove)}")
            selected = prompt_selection(to_remove, "Packages to remove?")
            if selected:
                installer.remove([current[p] for p in selected])
                for p in selected:
                    installed.pop(p, None)

        return installed

    def sync_local_packages(
        self, installer: PackageInstaller, source: DotsSource, current: dict[str, list[str]], desired: list[str]
    ) -> dict[str, list[str]]:
        
        if not getattr(self.args, "git", False):
            to_build = []
            to_rebuild = desired
        else:
            to_build = [p for p in desired if p not in current]
            to_rebuild = self.outdated_local_packages(installer, source, current, desired)
            
        to_remove = [p for p in current if p not in desired]
        installed = dict(current)

        if to_build:
            print()
            log(f"Building new local packages: {', '.join(to_build)}")
            installed.update(build_local_packages(installer, source, to_build))

        if to_rebuild:
            print()
            log(f"Rebuilding updated local packages: {', '.join(to_rebuild)}")
            installed.update(build_local_packages(installer, source, to_rebuild))

        if to_remove:
            print()
            info(f"Local packages no longer required: {', '.join(to_remove)}")
            selected = prompt_selection(to_remove, "Local packages to remove?")
            if selected:
                installer.remove([pkg for path in selected for pkg in current[path]])
                for path in selected:
                    installed.pop(path, None)

        return installed

    def outdated_local_packages(
        self, installer: PackageInstaller, source: DotsSource, current: dict[str, list[str]], desired: list[str]
    ) -> list[str]:
        """Repo paths whose installed packages are older than what the repo would build."""
        outdated = []
        for path in desired:
            if path not in current:
                continue

            directory = source.working_path(path)
            if not directory.is_dir():
                continue

            try:
                if installer.needs_rebuild(directory, current[path]):
                    outdated.append(path)
            except PackageError as e:
                # Failed to read PKGBUILD, leave it as-is
                warn(f"could not check {path} for updates, leaving as-is: {e}")

        return outdated

    def summarize(self, changeset: Changeset, new_files: list[Path], revived_files: list[Path]) -> None:
        print()
        conflicts = len(new_files) + len(revived_files)
        info(f"Updated {len(changeset.place)} file(s), removed {len(changeset.deletes)}, {conflicts} conflict(s).")
        if new_files:
            info("The following files were changed upstream but you had edited them locally.")
            info("Your versions were kept; the upstream versions were written alongside as .new:")
            for path in new_files:
                info(f"  {path}")
        if revived_files:
            info("These files were removed by you but changed upstream, so were not restored.")
            info("The upstream versions were written alongside as .new:")
            for path in revived_files:
                info(f"  {path}")
        if changeset.stale:
            info("These files are no longer managed but differ from what was installed, so were kept:")
            for path in changeset.stale:
                info(f"  {path}")


def _update_shell_with_pkgit(installer: PackageInstaller, noconfirm: bool) -> None:
    print()
    log("Updating Caelestia Shell...")

    src = detect_shell_management_source(installer)

    if src == "shell":
        info("Shell package already installed - update handled by system package sync")
        return

    if src == "manual":
        info("Manual shell marker present - skipping pkgit")
        return

    if src == "cli":
        pkg = shell_package_matching_cli(installer)
        try:
            info(f"CLI was installed via AUR - updating shell via the same source ({pkg})...")
            installer.install([pkg])
            info(f"Caelestia Shell updated via {pkg}")
            return
        except PackageError as e:
            warn(f"Failed to update {pkg} via the system package manager: {e}")
            info("Falling back to pkgit")

    if shutil.which("pkgit") is None:
        info("pkgit not found - shell will update from system paths")
        info("To enable pkgit package management, install pkgit-git from AUR:")
        info("  yay -S pkgit-git")
        return

    cmd = ["pkgit", "-qfi" if noconfirm else "-fi", "https://github.com/dim-ghub/caelestia-shell"]
    try:
        subprocess.run(cmd, check=True)
        info("Caelestia Shell updated successfully via pkgit")
    except subprocess.CalledProcessError as e:
        warn(f"Failed to update Caelestia Shell via pkgit: {e}")
        info("The shell will still function from system paths")
