import os
import shutil
import textwrap
from argparse import Namespace
from pathlib import Path

from caelestia.utils.dots.deployer import Deployer
from caelestia.utils.dots.legacy import (
    LEGACY_META_PKG,
    detect_legacy_repo,
    legacy_config_symlinks,
    legacy_symlinks,
    legacy_to_delete,
)
from caelestia.utils.dots.manifest import ComponentError, Manifest, ManifestError
from caelestia.utils.dots.misc import build_local_packages, build_manual_packages, run_hooks
from caelestia.utils.dots.packages import DEFAULT_AUR_HELPER, PackageError, PackageInstaller
from caelestia.utils.dots.source import DotsSource, SourceError
from caelestia.utils.dots.state import DotsState
from caelestia.utils.io import confirm, disable_input, fatal, info, log, pause, prompt_selection, warn
from caelestia.utils.paths import (
    config_backup_dir,
    config_dir,
)


def _parse_list_arg(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _deref_symlink(link: Path, target: Path) -> None:
    bak = link.rename(link.parent / f"{link.name}.bak")
    try:
        if target.is_dir():
            shutil.copytree(target, link, symlinks=True)
        else:
            shutil.copy2(target, link)
    except OSError:
        bak.rename(link)
        raise
    bak.unlink()


class Command:
    args: Namespace

    def __init__(self, args: Namespace) -> None:
        self.args = args

    def run(self) -> None:
        if self.args.noconfirm:
            disable_input()

        self.print_greeting()
        self.create_backup()
        legacy_dir = detect_legacy_repo()

        old_state = DotsState.load()

        source, tip, manifest = self.fetch_manifest(old_state)
        try:
            installer, packages, local_packages = self.install_packages(source, manifest, old_state)
        except PackageError as e:
            fatal(e)

        run_hooks(manifest, "post_package")
        self.dereference_legacy(legacy_dir)

        deployed = self.deploy_configs(source, manifest, old_state)
        run_hooks(manifest, "post_install")

        DotsState(
            aur_helper=getattr(installer, "helper", DEFAULT_AUR_HELPER),
            applied_rev=tip,
            enabled_components=manifest.enabled_components,
            packages=packages,
            local_packages=local_packages,
            deployed_files=deployed,
        ).save()

        self.migrate_legacy(installer, legacy_dir)
        self.print_done()

    def print_greeting(self) -> None:
        print(
            "\033[38;2;150;241;241m"
            + textwrap.dedent(
                r"""
                ╭─────────────────────────────────────────────────╮
                │      ______           __          __  _         │
                │     / ____/___ ____  / /__  _____/ /_(_)___ _   │
                │    / /   / __ `/ _ \/ / _ \/ ___/ __/ / __ `/   │
                │   / /___/ /_/ /  __/ /  __(__  ) /_/ / /_/ /    │
                │   \____/\__,_/\___/_/\___/____/\__/_/\__,_/     │
                │                                                 │
                ╰─────────────────────────────────────────────────╯
                """
            )
            + "\033[0m"
        )
        info("Welcome to the Caelestia dotfiles installer!")
        info("Here's a quick overview on what this command is going to do:")
        info("  - Install dependencies")
        info("  - Install config files")
        info("The installer does NOT set up hardware/system level configs (e.g. drivers). Please do this yourself.")
        pause()
        print()

    def create_backup(self) -> None:
        if config_dir.exists():
            if not confirm("Back up the config directory?", default=False):
                return

            log(f"Creating a backup of {config_dir}...")
            if config_backup_dir.exists():
                if not confirm("A backup already exists, overwrite?", default=False):
                    info("Not creating backup.")
                    return

                log("Deleting old backup...")
                shutil.rmtree(config_backup_dir)

            shutil.copytree(config_dir, config_backup_dir, symlinks=True)
            info(f"Created backup at {config_backup_dir}")

    def fetch_manifest(self, old_state: DotsState | None) -> tuple[DotsSource, str, Manifest]:
        print()
        log("Fetching dots repo...")
        source = DotsSource()
        try:
            source.ensure()
            tip = source.checkout_tip()
        except SourceError as e:
            fatal(e)

        enable = _parse_list_arg(self.args.enable_components)
        disable = _parse_list_arg(self.args.disable_components)

        try:
            manifest = source.manifest_at(tip)
            if enable is None and disable is None:
                if getattr(self.args, "reinstall", False):
                    all_comps = list(manifest.components.keys())
                    selected = prompt_selection(all_comps, "Components to reinstall?")
                    enable = selected
                    if old_state and old_state.enabled_components:
                        enable.extend([c for c in old_state.enabled_components if c not in selected])
                    disable = []
                elif getattr(self.args, "ask_all", False):
                    all_comps = list(manifest.components.keys())
                    selected = prompt_selection(all_comps, "Components to enable?")
                    enable = selected
                    disable = [comp for comp in all_comps if comp not in selected]
                elif old_state and old_state.enabled_components:
                    info(f"Previously enabled components: {', '.join(old_state.enabled_components)}")
                    all_comps = list(manifest.components.keys())
                    selected = prompt_selection(all_comps, "Modify components? (Select all components you want to keep/add):")
                    enable = selected
                    disable = [comp for comp in all_comps if comp not in selected]
                else:
                    optional = [name for name, comp in manifest.components.items() if not comp.default]
                    if optional:
                        enable = prompt_selection(optional, "Components to enable?")

            manifest.resolve_components(enable=enable, disable=disable)
        except (SourceError, ManifestError, ComponentError) as e:
            fatal(e)

        names = ", ".join(manifest.enabled_components) or "none"
        info(f"Enabled components: {names}")
        return source, tip, manifest

    def deploy_configs(self, source: DotsSource, manifest: Manifest, old_state: DotsState | None) -> dict[str, str]:
        print()
        log("Installing configs...")
        deployer = Deployer()

        # Place currently enabled entries
        for entry in manifest.enabled_entries():
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

        deployed = dict(deployer.deployed_files)
        if old_state and old_state.deployed_files:
            for old_dest, old_src in old_state.deployed_files.items():
                if old_dest not in deployed:
                    path = Path(old_dest)
                    if path.exists() or path.is_symlink():
                        use_sudo = not os.access(path.parent if path.parent.exists() else path, os.W_OK)
                        try:
                            deployer.remove(path, sudo=use_sudo)
                            info(f"Deleted -> {old_dest}")
                        except Exception as e:
                            warn(f"Failed to remove orphaned file {old_dest}: {e}")

        return deployed

    def install_packages(
        self, source: DotsSource, manifest: Manifest, old_state: DotsState | None
    ) -> tuple[PackageInstaller, dict[str, str], dict[str, list[str]]]:
        installer = PackageInstaller.get(self.args.aur_helper, self.args.noconfirm)

        if old_state:
            new_desired_pkgs = set(manifest.enabled_packages())
            orphaned_pkg_keys = set(old_state.packages.keys()) - new_desired_pkgs
            pkgs_to_remove = [old_state.packages[key] for key in orphaned_pkg_keys if key in old_state.packages]

            new_local_dirs = set(manifest.enabled_local_packages())
            orphaned_local_dirs = set(old_state.local_packages.keys()) - new_local_dirs
            for local_dir in orphaned_local_dirs:
                pkgs_to_remove.extend(old_state.local_packages[local_dir])

            if pkgs_to_remove:
                print()
                log(f"Uninstalling {len(pkgs_to_remove)} removed packages...")
                try:
                    installer.remove(pkgs_to_remove)
                except PackageError as e:
                    warn(f"Failed to remove some orphaned packages: {e}")

        packages = {}
        desired = manifest.enabled_packages()
        if desired:
            print()
            log("Installing packages...")
            packages = dict(zip(desired, installer.install(desired)))

        if old_state and old_state.packages:
            for pkg, real in old_state.packages.items():
                if pkg in manifest.all_known_packages() and pkg not in packages:
                    packages[pkg] = real

        local_packages = {}
        local_dirs = manifest.enabled_local_packages()
        if local_dirs:
            print()
            log("Building local packages...")
            local_packages = build_local_packages(installer, source, local_dirs)

        manual_pkgs = []
        for name in manifest.enabled_components:
            manual_pkgs.extend(manifest.components[name].manual_packages)

        if manual_pkgs:
            build_manual_packages(installer, manual_pkgs)

        return installer, packages, local_packages

    def dereference_legacy(self, legacy_dir: Path | None) -> None:
        symlinks = legacy_symlinks(legacy_dir)
        if not symlinks:
            return

        print()
        log("Preserving content from legacy symlinks...")
        for path in symlinks:
            target = path.resolve()
            if not target.exists():
                continue

            try:
                _deref_symlink(path, target)
                info(f"Copied {target} -> {path}")
            except OSError as e:
                warn(f"failed to preserve {path}: {e}")

    def deref_backup_syms(self, legacy_dir: Path | None) -> None:
        if not config_backup_dir.is_dir():
            return

        for link in legacy_config_symlinks(config_backup_dir, legacy_dir):
            target = link.resolve()
            if not target.exists():
                continue

            try:
                _deref_symlink(link, target)
            except OSError as e:
                warn(f"failed to preserve {link} in backup: {e}")

    def migrate_legacy(self, installer: PackageInstaller, legacy_dir: Path | None) -> None:
        to_delete = legacy_to_delete(legacy_dir)
        meta_installed = installer.is_installed(LEGACY_META_PKG)
        if not to_delete and not meta_installed:
            return

        print()
        log("Found a legacy Caelestia installation...")
        if not confirm("Clear legacy installation?"):
            return

        deployer = Deployer()
        try:
            self.deref_backup_syms(legacy_dir)
            for path in to_delete:
                deployer.remove(path)
                info(f"Deleted {path}")

            if meta_installed:
                log("Removing legacy meta package...")
                installer.remove([LEGACY_META_PKG])
        except (OSError, PackageError) as e:
            warn(f"could not fully clear the legacy installation: {e}")

    def print_done(self) -> None:
        print()
        info("All done! Caelestia has been installed.")