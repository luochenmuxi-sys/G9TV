import os
import subprocess
import tempfile
import uuid
import folder_paths

def resolve(path):
    path = path.strip().strip('"')
    if os.path.isfile(path):
        return os.path.abspath(path)
    for root in (folder_paths.get_input_directory(), folder_paths.get_output_directory()):
        candidate = os.path.join(root, path)
        if os.path.isfile(candidate):
            return candidate
    raise ValueError("找不到素材文件：" + path)

class StudioClip:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"enabled": ("BOOLEAN", {"default": False}), "file_path": ("STRING", {"default": "", "multiline": False}), "start_seconds": ("FLOAT", {"default": 0, "min": 0}), "end_seconds": ("FLOAT", {"default": 0, "min": 0})}}
    RETURN_TYPES = ("STUDIO_CLIP",)
    FUNCTION = "clip"
    CATEGORY = "Simple Studio"
    def clip(self, enabled, file_path, start_seconds, end_seconds):
        if not enabled:
            return (None,)
        if end_seconds and end_seconds <= start_seconds:
            raise ValueError("结束时间必须大于开始时间；0表示使用到片尾")
        return ({"path": resolve(file_path), "start": start_seconds, "end": end_seconds},)

class StudioEditExport:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"width": ("INT", {"default": 1280, "min": 256, "max": 3840, "step": 2}), "height": ("INT", {"default": 720, "min": 256, "max": 3840, "step": 2}), "fps": ("INT", {"default": 24, "min": 1, "max": 60}), "crf": ("INT", {"default": 20, "min": 12, "max": 35}), "music_path": ("STRING", {"default": ""}), "music_volume": ("FLOAT", {"default": 0.3, "min": 0, "max": 2}), "keep_original_audio": ("BOOLEAN", {"default": True})}, "optional": {f"clip_{i}": ("STUDIO_CLIP",) for i in range(1, 5)}}
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("saved_video",)
    OUTPUT_NODE = True
    FUNCTION = "export"
    CATEGORY = "Simple Studio"
    @classmethod
    def IS_CHANGED(cls, **kwargs):
        return float("nan")
    def export(self, width, height, fps, crf, music_path, music_volume, keep_original_audio, **kwargs):
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        clips = [kwargs.get(f"clip_{i}") for i in range(1, 5) if kwargs.get(f"clip_{i}")]
        if not clips:
            raise ValueError("请至少启用一个片段，并填写视频文件路径")
        width = width // 2 * 2
        height = height // 2 * 2
        dest = os.path.join(folder_paths.get_output_directory(), "Studio", "Edits")
        os.makedirs(dest, exist_ok=True)
        output = os.path.join(dest, "edit_" + uuid.uuid4().hex[:12] + ".mp4")
        def run(args):
            result = subprocess.run([ff, "-hide_banner", "-y", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode:
                raise RuntimeError(result.stderr[-4000:])
        with tempfile.TemporaryDirectory(prefix="studio-edit-") as temp:
            files = []
            for i, clip in enumerate(clips):
                probe = subprocess.run([ff, "-hide_banner", "-i", clip["path"]], capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                has_audio = "Audio:" in probe.stderr and keep_original_audio
                args = ["-ss", str(clip["start"]), "-i", clip["path"]]
                if not has_audio:
                    args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                if clip["end"]:
                    args += ["-t", str(clip["end"] - clip["start"])]
                path = os.path.join(temp, f"clip{i}.mp4")
                args += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0", "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}", "-c:v", "libx264", "-preset", "fast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", path]
                run(args)
                files.append(path)
            listing = os.path.join(temp, "list.txt")
            with open(listing, "w", encoding="utf-8") as file:
                file.write("\n".join("file '" + p.replace("\\", "/") + "'" for p in files))
            merged = os.path.join(temp, "merged.mp4")
            run(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", merged])
            if music_path.strip():
                run(["-i", merged, "-stream_loop", "-1", "-i", resolve(music_path), "-filter_complex", f"[1:a]volume={music_volume}[m];[0:a][m]amix=inputs=2:duration=first:normalize=0[a]", "-map", "0:v:0", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-shortest", "-movflags", "+faststart", output])
            else:
                import shutil
                shutil.copyfile(merged, output)
        return {"ui": {"text": ["导出完成：" + output]}, "result": (output,)}
