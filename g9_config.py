"""Portable data paths, with compatibility for existing G9TV installations."""
import os
from pathlib import Path
import folder_paths

legacy = Path('D:/ComfyUI/StudioProjects')
PROJECTS = Path(os.environ['G9TV_PROJECTS']) if os.environ.get('G9TV_PROJECTS') else (legacy if legacy.is_dir() else Path(folder_paths.get_user_directory()) / 'G9TV' / 'projects')


def moss_model_path():
    if os.environ.get('G9TV_MOSS_PATH'):
        return os.environ['G9TV_MOSS_PATH']
    for root in [Path(folder_paths.models_dir), Path('D:/ComfyUI/app/ComfyUI/models')]:
        path = root / 'moss_image/MOSS-Image-SFT'
        if path.is_dir():
            return str(path)
    return str(Path(folder_paths.models_dir) / 'moss_image/MOSS-Image-SFT')
