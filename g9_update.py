"""User-initiated G9TV updates. No models or project data are replaced."""
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

REPOSITORY = 'luochenmuxi-sys/G9TV'
MANIFEST_URL = f'https://raw.githubusercontent.com/{REPOSITORY}/main/update-manifest.json'
ROOT = Path(__file__).resolve().parent
MAX_PACKAGE = 20 * 1024 * 1024


def read_url(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'G9TV-Updater/1.0'})
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('更新文件超出大小限制')
    return data


def manifest():
    data = json.loads(read_url(MANIFEST_URL, 128 * 1024))
    expected = f'https://raw.githubusercontent.com/{REPOSITORY}/main/dist/G9TV-plugin.zip'
    if data.get('package_url') != expected or not re.fullmatch(r'[a-f0-9]{64}', data.get('sha256', '')):
        raise ValueError('更新清单格式或下载地址不正确')
    if not re.fullmatch(r'\d+\.\d+\.\d+', data.get('version', '')):
        raise ValueError('版本号格式不正确')
    return data


def current_version(root=ROOT):
    path = root / 'version.json'
    return json.loads(path.read_text(encoding='utf-8'))['version'] if path.exists() else '0.0.0'


def newer(candidate, installed):
    return tuple(map(int, candidate.split('.'))) > tuple(map(int, installed.split('.')))


def package_files(content, expected_hash):
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError('下载文件校验失败，没有修改安装目录')
    result = {}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_PACKAGE:
            raise ValueError('更新解压体积过大')
        for item in archive.infolist():
            if item.is_dir():
                continue
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename:
                raise ValueError('更新包包含非法路径')
            if len(path.parts) == 1:
                allowed = path.suffix == '.py' or path.name in {'version.json', 'Update-G9TV.cmd', 'requirements.txt', 'README.md'}
            else:
                allowed = path.parts[0] in {'web', 'prompt-guides'} and path.suffix in {'.py', '.js', '.mjs', '.css', '.html', '.md', '.txt'}
            if not allowed or path.as_posix() in result:
                raise ValueError('更新包包含不允许的文件：' + item.filename)
            result[path.as_posix()] = archive.read(item)
    if not {'__init__.py', 'director.py', 'version.json'} <= result.keys():
        raise ValueError('更新包不完整')
    return result


def atomic_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.g9-new')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def restore(backup, root=ROOT):
    receipt = json.loads((backup / 'receipt.json').read_text(encoding='utf-8'))
    for name, existed in reversed(list(receipt.items())):
        target = root / name
        if not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('备份路径不正确')
        if existed:
            atomic_file(target, (backup / 'files' / name).read_bytes())
        else:
            target.unlink(missing_ok=True)


def install(files, root=ROOT):
    backups = root / '.g9-backups'
    backups.mkdir(exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=time.strftime('%Y%m%d-%H%M%S-'), dir=backups))
    receipt = {}
    for name in files:
        target = root / name
        if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
            raise ValueError('安装目录含有不安全的路径')
        receipt[name] = target.exists()
        if target.exists():
            saved = backup / 'files' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
    (backup / 'receipt.json').write_text(json.dumps(receipt), encoding='utf-8')
    try:
        for name, data in files.items():
            atomic_file(root / name, data)
    except Exception:
        restore(backup, root)
        raise
    return backup


def apply_update():
    info = manifest()
    if not newer(info['version'], current_version()):
        return {'updated': False, 'version': current_version()}
    content = read_url(info['package_url'], MAX_PACKAGE)
    files = package_files(content, info['sha256'])
    if json.loads(files['version.json'])['version'] != info['version']:
        raise ValueError('更新包版本与清单不一致')
    backup = install(files)
    return {'updated': True, 'version': info['version'], 'backup': str(backup), 'restartRequired': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if args.rollback:
        backups = sorted((ROOT / '.g9-backups').glob('*/receipt.json'))
        if not backups:
            raise ValueError('没有可恢复的更新备份')
        restore(backups[-1].parent)
        print('已恢复上一个版本，请重启 ComfyUI。')
    elif args.check:
        print(json.dumps({'current': current_version(), 'latest': manifest()['version']}, ensure_ascii=False))
    else:
        print(json.dumps(apply_update(), ensure_ascii=False))
        print('更新后请重启 ComfyUI。更新期间请勿生成或修改项目。')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('更新失败：' + str(error))
        raise SystemExit(1)
