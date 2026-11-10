"""Video enhancement adapters for the local G9 TV task queue."""
from pathlib import Path

class StudioUpscaleVideoSource:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required":{"video":("STRING",)}}
    RETURN_TYPES=("VIDEO",)
    FUNCTION="load"
    CATEGORY="Simple Studio/Director"
    def load(self,video):
        from .director import ROOT
        from comfy_api.latest import InputImpl
        path=Path(video).resolve()
        if not path.is_relative_to(ROOT.resolve()) or not path.is_file():
            raise ValueError("源视频必须位于当前项目素材目录")
        return (InputImpl.VideoFromFile(str(path)),)

def compile_upscale(project,card,refs,seed):
    engine=card.get('settings',{}).get('upscaleEngine','seedvr2')
    if engine=='open_h3':
        from .open_h3 import compile_open_h3
        return compile_open_h3(project,card,refs,seed)
    if engine!='seedvr2':raise ValueError('未知视频转高清引擎')
    import folder_paths
    import nodes
    from .director import media_path, ident
    needed=('SeedVR2LoadDiTModel','SeedVR2LoadVAEModel','SeedVR2VideoUpscaler')
    if any(name not in nodes.NODE_CLASS_MAPPINGS for name in needed):
        raise ValueError("SeedVR2插件未加载，请安装插件并重启ComfyUI")
    if 'source_video' not in refs:raise ValueError("请连接一张视频素材或视频生成结果卡")
    model='seedvr2_ema_3b_fp8_e4m3fn.safetensors'
    vae_model='ema_vae_fp16.safetensors'
    for filename in (model,vae_model):
        if not folder_paths.get_full_path('seedvr2',filename):
            raise ValueError("缺少SeedVR2模型："+filename+"，请放入models/SEEDVR2")
    settings=card.get('settings',{})
    resolution=int(settings.get('upscaleResolution',1080))
    if resolution not in (720,1080,1440,2160):raise ValueError("请选择720P、1080P、1440P或4K")
    batch=int(settings.get('upscaleBatch',5))
    if batch not in (1,5,9,13):raise ValueError("批次大小请选择1、5、9或13")
    if not 0<=seed<=4294967295:raise ValueError("SeedVR2种子需在0至4294967295之间")
    p={}
    def add(role,cls,**inputs):
        p[role]={'class_type':cls,'inputs':inputs,'_meta':{'title':role,'studio_role':role}}
        return [role,0]
    source=add('upscale_source','StudioUpscaleVideoSource',video=str(media_path(refs['source_video'])))
    add('upscale_components','GetVideoComponents',video=source)
    dit=add('upscale_model','SeedVR2LoadDiTModel',model=model,device='cuda:0',blocks_to_swap=24,swap_io_components=False,offload_device='cpu',cache_model=False,attention_mode='sdpa')
    vae=add('upscale_vae','SeedVR2LoadVAEModel',model=vae_model,device='cuda:0',encode_tiled=True,encode_tile_size=512,encode_tile_overlap=64,decode_tiled=True,decode_tile_size=512,decode_tile_overlap=64,tile_debug='false',offload_device='cpu',cache_model=False)
    frames=add('upscale_frames','SeedVR2VideoUpscaler',image=['upscale_components',0],dit=dit,vae=vae,seed=seed,resolution=resolution,max_resolution=round(resolution*16/9/2)*2,batch_size=batch,uniform_batch_size=True,color_correction='lab',temporal_overlap=3 if batch>1 else 0,prepend_frames=0,input_noise_scale=0.0,latent_noise_scale=0.0,offload_device='cpu',enable_debug=False)
    add('upscale_save','VHS_VideoCombine',images=frames,audio=['upscale_components',1],frame_rate=['upscale_components',2],loop_count=0,filename_prefix=f"Studio/{ident(project['id'])}/{ident(card['id'])}/SeedVR2_{resolution}P",format='video/h264-mp4',pix_fmt='yuv420p',crf=18,save_metadata=True,trim_to_audio=False,pingpong=False,save_output=True)
    return p,[{'slot':'source_video','assetId':refs['source_video']['id'],'tag':'SeedVR2源视频'}]
