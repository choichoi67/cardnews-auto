"""Install pinned official PhotoCraft web/CLI releases without changing their contents."""
import hashlib
from pathlib import Path
import urllib.request
import zipfile


VERSION = '0.10.0'
PACKAGES = {
    'photocraft-web-0.10.0.zip': 'b121472d1cdc2f24530b8239c4f32843afdbc1da994244e98a2ce4835b410590',
    'photocraft-0.10.0-windows-x64-portable.zip': '66b7e47d66589eb6947c8d7417c80fa44621cf83823ffdc47f42024eb03bd5d2',
}


def install(root):
    root.mkdir(parents=True, exist_ok=True)
    for name, digest in PACKAGES.items():
        archive = root / name
        if not archive.exists():
            urllib.request.urlretrieve(
                f'https://github.com/storytold/photocraft/releases/download/v{VERSION}/{name}', archive)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Checksum mismatch: {archive}')
        with zipfile.ZipFile(archive) as package:
            for member in package.namelist():
                if not (root / member).resolve().is_relative_to(root.resolve()):
                    raise ValueError('Archive path escapes installation directory')
            package.extractall(root)
        print(f'Installed and verified: {name}')


if __name__ == '__main__':
    install(Path(__file__).resolve().parents[1] / '.tools' / 'photocraft')
