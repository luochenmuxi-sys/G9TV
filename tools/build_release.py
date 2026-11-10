"""Build the explicit program-only package and matching update manifest."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
version = json.loads((root / 'version.json').read_text(encoding='utf-8'))['version']
files = [*root.glob('*.py'), root / 'version.json', root / 'Update-G9TV.cmd', root / 'requirements.txt', root / 'README.md']
for folder in ['web', 'prompt-guides']:
    files.extend(path for path in (root / folder).rglob('*') if path.is_file() and '__pycache__' not in path.parts)
(root / 'dist').mkdir(exist_ok=True)
package = root / 'dist/G9TV-plugin.zip'
with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(files):
        archive.write(path, path.relative_to(root).as_posix())
manifest = {'version': version, 'package_url': 'https://raw.githubusercontent.com/luochenmuxi-sys/G9TV/main/dist/G9TV-plugin.zip', 'sha256': hashlib.sha256(package.read_bytes()).hexdigest(), 'notes': '独立剪辑窗口、素材按需加载、声音与多轨编辑、按需联网更新。'}
(root / 'update-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(f'{version}: {len(files)} program files, {package.stat().st_size} bytes')
