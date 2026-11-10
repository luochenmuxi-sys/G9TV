"""Open-H3 refinement graphs and source-video timing for the director canvas."""
import math

import comfy.utils
import folder_paths
import nodes


class StudioOpenH3Prepare:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'images':('IMAGE',),'fps':('FLOAT',{'default':24.0,'min':0.01}),
                            'resolution':([720,1080,1440,2160],)}}

    RETURN_TYPES=('IMAGE','INT','INT','INT','STUDIO_H3_TIMING')
    RETURN_NAMES=('draft','width','height','length','timing')
    FUNCTION='prepare'
    CATEGORY='Simple Studio/Director'

    def prepare(self,images,fps,resolution):
        if not math.isfinite(fps) or fps<=0 or images.shape[0]==0:
            raise ValueError('源视频没有有效帧率或画面')
        count,height,width=images.shape[:3]
        long_edge={720:1280,1080:1920,1440:2560,2160:3840}[resolution]
        output_width=max(2,round(width*long_edge/max(width,height)/2)*2)
        output_height=max(2,round(height*long_edge/max(width,height)/2)*2)
        target_width=max(32,round(output_width/32)*32)
        target_height=max(32,round(output_height/32)*32)
        draft_edge=min(long_edge,1280,max(long_edge/4,min(max(width,height),1280)))
        draft_width=max(32,round(target_width*draft_edge/long_edge/32)*32)
        draft_height=max(32,round(target_height*draft_edge/long_edge/32)*32)
        native_count=max(5,math.ceil(count/fps*24))
        native_count+=((5-native_count)%17)
        indices=[min(count-1,round(i*fps/24)) for i in range(native_count)]
        draft=comfy.utils.common_upscale(images[indices,...,:3].movedim(-1,1),draft_width,draft_height,'lanczos','disabled').movedim(1,-1)
        timing={'frames':count,'fps':fps,'width':output_width,'height':output_height}
        return draft,target_width,target_height,native_count,timing


class StudioOpenH3Restore:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'images':('IMAGE',),'timing':('STUDIO_H3_TIMING',)}}

    RETURN_TYPES=('IMAGE',)
    FUNCTION='restore'
    CATEGORY='Simple Studio/Director'

    def restore(self,images,timing):
        indices=[min(images.shape[0]-1,round(i*24/timing['fps'])) for i in range(timing['frames'])]
        frames=images[indices]
        if tuple(frames.shape[1:3])!=(timing['height'],timing['width']):
            frames=comfy.utils.common_upscale(frames.movedim(-1,1),timing['width'],timing['height'],'lanczos','disabled').movedim(1,-1)
        return (frames,)


def compile_open_h3(project,card,refs,seed):
    from .director import media_path,ident

    settings=card.get('settings',{})
    memory=settings.get('openH3Memory','low_vram')
    if memory not in ('low_vram','full'):raise ValueError('请选择省显存或整段精修模式')
    required=('AILab_OpenH3_Regenerate_2K','MinimaxH3LatentUpscaler3D',
              'StudioOpenH3Prepare','StudioOpenH3Restore','EmptyMiniMaxH3LatentAV',
              'LTXVSeparateAVLatent','LTXVConcatAVLatent','MiniMaxH3ActivationChunkStar7')
    if memory=='low_vram':required+=('MMH3TemporalSplitParamsV10','MMH3SplitUpscale','MMH3SpatialSplitParamsV10')
    missing=[name for name in required if name not in nodes.NODE_CLASS_MAPPINGS]
    if missing:raise ValueError('Open-H3节点未加载，请重启ComfyUI：'+', '.join(missing))
    if 'source_video' not in refs:raise ValueError('请连接视频素材或视频生成结果卡')
    models={
        'diffusion_models':'minimax_h3_ref2va_pruned_int8_convrot.safetensors',
        'text_encoders':'qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors',
        'vae':'minimax_h3_video_vae_fp16.safetensors',
        'latent_upscale_models':'minimax_h3_latent_upscaler_3d_conv_v1_bf16.safetensors',
        'loras':'minmax/open_h3_regenerate_2k_bf16.safetensors',
    }
    sharp=settings.get('openH3Sharp',True)
    for folder,name in list(models.items())+([('loras','minmax/sharp_curve_branches.safetensors')] if sharp else []):
        if not folder_paths.get_full_path(folder,name):raise ValueError('缺少Open-H3模型：'+name)
    resolution=int(settings.get('upscaleResolution',1440))
    if resolution not in (720,1080,1440,2160):raise ValueError('请选择720P、1080P、1440P或4K')
    steps=str(settings.get('openH3Steps','3'))
    if steps not in ('2','3'):raise ValueError('Open-H3精修步数请选择2或3')
    if not 0<=seed<=9007199254740991:raise ValueError('种子超出安全整数范围')
    text=str(settings.get('openH3Prompt','')).strip() or 'Preserve the original scene, subject identity, composition and motion. Restore realistic fine details and natural textures.'
    p={}
    def add(role,cls,**inputs):
        p[role]={'class_type':cls,'inputs':inputs,'_meta':{'title':role,'studio_role':role}}
        return [role,0]
    source=add('upscale_source','StudioUpscaleVideoSource',video=str(media_path(refs['source_video'])))
    add('upscale_components','GetVideoComponents',video=source)
    add('h3_prepare','StudioOpenH3Prepare',images=['upscale_components',0],fps=['upscale_components',2],resolution=resolution)
    vae=add('h3_vae','VAELoader',vae_name=models['vae'])
    encoded=add('h3_encode','VAEEncode',pixels=['h3_prepare',0],vae=vae)
    enlarged=add('h3_upscale','MinimaxH3LatentUpscaler3D',latent=encoded,model_name=models['latent_upscale_models'],
                 mode='target dimensions',align=32,enable_temporal_chunking=True,force_unload=True,device='cuda',precision='bf16',
                 **{'mode.width':['h3_prepare',1],'mode.height':['h3_prepare',2]})
    empty=add('h3_empty_av','EmptyMiniMaxH3LatentAV',width=['h3_prepare',1],height=['h3_prepare',2],length=['h3_prepare',3])
    add('h3_audio_latent','LTXVSeparateAVLatent',av_latent=empty)
    joint=add('h3_joint','LTXVConcatAVLatent',video_latent=enlarged,audio_latent=['h3_audio_latent',1])
    model=add('h3_model','UNETLoader',unet_name=models['diffusion_models'],weight_dtype='default')
    model=add('h3_shift','MiniMaxH3SigmaShift',model=model,shift_video=6.0,shift_audio=3.0)
    model=add('h3_memory','MiniMaxH3ActivationChunkStar7',model=model,chunk_tokens=4096,auto_halve_on_oom=True,verbose=True,
              mlp_chunk_tokens=4096,disable_dynamic_prefetch='auto',qkv_chunk_tokens=4096,out_proj_chunk_tokens=4096,
              reuse_mlp_weights=True,attention_backend='comfy_kitchen_int8')
    add('h3_refiner','AILab_OpenH3_Regenerate_2K',model=model,model_name='Open-H3-Regenerate-2K (BF16)',strength=1.0,
        sharp_curve=sharp,steps=steps,lock_audio=True,unload_model=False,latent=joint)
    clip=add('h3_encoder','CLIPLoader',clip_name=models['text_encoders'],type='minimax',device='default')
    positive=add('h3_prompt','CLIPTextEncode',clip=clip,text=text)
    noise=add('h3_noise','RandomNoise',noise_seed=seed)
    sampler=add('h3_sampler','KSamplerSelect',sampler_name='euler')
    if memory=='low_vram':
        temporal=add('h3_temporal','MMH3TemporalSplitParamsV10',chunk_frames=56,temporal_overlap_frames=22,
                     anchor_strength=0.999,motion_anchor_frames='5',identity_anchor_frames=0)
        split={}
        if resolution==2160:
            split['spatial_split_param']=add('h3_spatial','MMH3SpatialSplitParamsV10',tile_width=1280,tile_height=768,
                                            overlap_ratio=0.25,fade_ratio=0.5,min_tile_size=512,seam_denoise=1.0)
        sample=add('h3_sample','MMH3SplitUpscale',model=['h3_refiner',0],conditioning=positive,latent=['h3_refiner',1],
                   noise=noise,sampler=sampler,sigmas=['h3_refiner',2],cfg=1.0,temporal_split_param=temporal,
                   seam_polish='off',color_match=True,**split)
    else:
        guider=add('h3_guider','BasicGuider',model=['h3_refiner',0],conditioning=positive)
        sample=add('h3_sample','SamplerCustomAdvanced',noise=noise,guider=guider,sampler=sampler,sigmas=['h3_refiner',2],latent_image=['h3_refiner',1])
    decoded=add('h3_decode','VAEDecodeTiled',samples=sample,vae=vae,tile_size=512,overlap=64,temporal_size=32,temporal_overlap=8)
    frames=add('h3_restore','StudioOpenH3Restore',images=decoded,timing=['h3_prepare',4])
    add('upscale_save','VHS_VideoCombine',images=frames,audio=['upscale_components',1],frame_rate=['upscale_components',2],
        loop_count=0,filename_prefix=f"Studio/{ident(project['id'])}/{ident(card['id'])}/OpenH3_{resolution}P",
        format='video/h264-mp4',pix_fmt='yuv420p',crf=18,save_metadata=True,trim_to_audio=False,pingpong=False,save_output=True)
    return p,[{'slot':'source_video','assetId':refs['source_video']['id'],'tag':'Open-H3源视频'}]
