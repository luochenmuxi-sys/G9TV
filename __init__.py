class StudioImageSource:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"source": (["none", "upload", "generate"],)}, "optional": {"uploaded": ("IMAGE", {"lazy": True}), "generated": ("IMAGE", {"lazy": True})}}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "select"
    CATEGORY = "Simple Studio"
    def check_lazy_status(self, source, uploaded=None, generated=None):
        if source == "upload" and uploaded is None:
            return ["uploaded"]
        if source == "generate" and generated is None:
            return ["generated"]
        return []
    def select(self, source, uploaded=None, generated=None):
        return (generated if source == "generate" else uploaded if source == "upload" else None,)

class StudioOptionalImage:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths
        import os
        files = [f for f in os.listdir(folder_paths.get_input_directory()) if os.path.isfile(os.path.join(folder_paths.get_input_directory(), f))]
        return {"required": {"enabled": ("BOOLEAN", {"default": False}), "image": (["未选择"] + sorted(files), {"image_upload": True})}}
    RETURN_TYPES = ("IMAGE",)
    FUNCTION = "load"
    CATEGORY = "Simple Studio"
    @classmethod
    def VALIDATE_INPUTS(cls, enabled, image):
        import folder_paths
        if not enabled:
            return True
        return True if image != "未选择" and folder_paths.exists_annotated_filepath(image) else "请上传图片，或关闭此参考入口"
    def load(self, enabled, image):
        if not enabled:
            return (None,)
        import folder_paths
        import numpy as np
        import torch
        from PIL import Image, ImageOps
        with Image.open(folder_paths.get_annotated_filepath(image)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((1280, 1280))
            return (torch.from_numpy(np.asarray(im).astype(np.float32) / 255.0).unsqueeze(0),)

NODE_CLASS_MAPPINGS = {"StudioImageSource": StudioImageSource, "StudioOptionalImage": StudioOptionalImage}
WEB_DIRECTORY = "./web"
NODE_DISPLAY_NAME_MAPPINGS = {"StudioImageSource": "图片来源 · none关闭 / upload上传 / generate生图", "StudioOptionalImage": "可选图片 · 关闭时无需上传"}
from .editing import StudioClip, StudioEditExport
NODE_CLASS_MAPPINGS.update({"StudioClip": StudioClip, "StudioEditExport": StudioEditExport})
NODE_DISPLAY_NAME_MAPPINGS.update({"StudioClip": "视频片段 · 开关与裁剪", "StudioEditExport": "按顺序拼接 · 配乐与导出"})
from .studio_nodes import NODE_CLASS_MAPPINGS as DIRECTOR_NODES
NODE_CLASS_MAPPINGS.update(DIRECTOR_NODES)
from . import director
from . import prompt_optimizer
from . import asset_download
from . import tail_frame
from . import timeline

from .video_upscale import StudioUpscaleVideoSource
NODE_CLASS_MAPPINGS["StudioUpscaleVideoSource"] = StudioUpscaleVideoSource
from .open_h3 import StudioOpenH3Prepare, StudioOpenH3Restore
NODE_CLASS_MAPPINGS.update({"StudioOpenH3Prepare":StudioOpenH3Prepare,"StudioOpenH3Restore":StudioOpenH3Restore})

from . import sound

from . import audio_extract

from . import editor

from . import update_service
