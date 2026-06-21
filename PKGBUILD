pkgname='caelestia-cli'
pkgver=1.1.0
pkgrel=1
pkgdesc='The main cli for the Caelestia dotfiles'
arch=('any')
url='https://github.com/GwynnN7/caelestia-cli'
license=('GPL-3.0-only')
depends=('python' 'python-pillow' 'python-materialyoucolor' 'libnotify' 'swappy' 'grim' 'dart-sass'
         'wl-clipboard' 'slurp' 'gpu-screen-recorder' 'dconf' 'cliphist' 'fuzzel')
optdepends=('caelestia-shell: shell control and screenshot function')
makedepends=('python-build' 'python-installer' 'python-hatch' 'python-hatch-vcs')
provides=('caelestia-cli')
conflicts=('caelestia-cli-git')

source=("git+https://github.com/GwynnN7/caelestia-cli.git")
sha256sums=('SKIP')

build() {
    cd "${srcdir}/caelestia-cli"
    python -m build --wheel --no-isolation
}

package() {
    cd "${srcdir}/caelestia-cli"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 ./completions/caelestia.fish "$pkgdir"/usr/share/fish/vendor_completions.d/caelestia.fish
}