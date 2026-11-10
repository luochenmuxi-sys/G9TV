"""Local director canvas: projects, assets, typed graph and native Comfy queue adapters."""
import asyncio, copy, json, math, os, re, shutil, time, uuid
from pathlib import Path
from aiohttp import web
import folder_paths
from server import PromptServer
from .collaboration import require_editor
from .video_output import video_dimensions

from .g9_config import PROJECTS, moss_model_path
ROOT=PROJECTS
ROOT.mkdir(parents=True,exist_ok=True)
WEB=Path(__file__).parent/'web'
ROUTES=PromptServer.instance.routes
LOCK=asyncio.Lock()
KINDS={'text','image','video','audio','imagegen','videogen','upscalegen'}
SLOTS={'upscalegen':{'source_video':'video'},'image':{'source_video':'video'},'imagegen':{'prompt':'text',**{f'role{i}':'image' for i in range(1,5)},'scene':'image','style':'image'},'videogen':{'prompt':'text','first':'image','last':'image',**{f'role{i}':'image' for i in range(1,5)},'normal':'video','white':'video','depth':'video'}}

def uid():return str(uuid.uuid4())
def ident(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',str(value)):raise ValueError('无效标识')
    return str(value)
def directory(pid):
    p=ROOT/ident(pid);p.mkdir(exist_ok=True);return p
def atomic(path,data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,path)
def read(pid):return json.loads((directory(pid)/'project.json').read_text(encoding='utf-8'))
def write(project):
    project['updated']=time.time();atomic(directory(project['id'])/'project.json',project)
def jobs(pid):
    p=directory(pid)/'jobs.json';return json.loads(p.read_text(encoding='utf-8')) if p.exists() else []
def savejobs(pid,data):atomic(directory(pid)/'jobs.json',data)
def validate(project):
    cards=project.get('cards',[]);edges=project.get('edges',[])
    if len(cards)>500 or len(edges)>3000:raise ValueError('项目过大')
    by={c['id']:c for c in cards}
    if len(by)!=len(cards):raise ValueError('卡片标识重复')
    used=set();graph={k:[] for k in by}
    for c in cards:
        ident(c['id'])
        if c['kind'] not in KINDS:raise ValueError('未知卡片类型')
    for e in edges:
        a,b=by.get(e['from']),by.get(e['to'])
        if not a or not b:raise ValueError('连线卡片不存在')
        output={'text':'text','image':'image','video':'video','audio':'audio','imagegen':'image','videogen':'video','upscalegen':'video'}[a['kind']]
        if SLOTS.get(b['kind'],{}).get(e['slot'])!=output:raise ValueError('连线类型不匹配')
        key=(b['id'],e['slot'])
        if key in used:raise ValueError('一个输入槽只能连接一个素材')
        used.add(key);graph[a['id']].append(b['id'])
    visiting=set();done=set()
    def dfs(n):
        if n in visiting:raise ValueError('画布不允许循环连接')
        if n in done:return
        visiting.add(n)
        for to in graph[n]:dfs(to)
        visiting.remove(n);done.add(n)
    for n in by:dfs(n)

def media_path(asset):
    base=ROOT.resolve()
    p=(base/asset['path']).resolve()
    if not p.is_relative_to(base) or not p.is_file():raise ValueError('素材文件不存在：'+asset.get('name',''))
    return p
def asset_record(pid,path,name=None):
    p=Path(path);ext=p.suffix.lower();kind='image' if ext in {'.png','.jpg','.jpeg','.webp','.bmp'} else 'audio' if ext in {'.wav','.mp3','.flac','.ogg','.m4a','.aac'} else 'video'
    base=ROOT; rel=p.relative_to(base).as_posix()
    from PIL import Image
    a={'id':uid(),'name':name or p.name,'kind':kind,'path':rel,'created':time.time()}
    if kind=='image':
        with Image.open(p) as im:a.update(width=im.width,height=im.height)
    return a

def references(project,card):
    by={c['id']:c for c in project['cards']}; assets={a['id']:a for a in project['assets']}; refs={}; mapping=[]
    for e in project['edges']:
        if e['to']!=card['id']:continue
        src=by[e['from']]
        if e['slot']=='prompt':refs['prompt']=src.get('text','');continue
        # Resolve the current source version when compiling. Submitted jobs retain
        # their own compiled prompt and project snapshot, including on retry.
        aid=src.get('selectedAssetId') if src['kind'] in ('imagegen','videogen','upscalegen') else src.get('assetId')
        if not aid:raise ValueError(f"{card['title']} / {e['slot']}：上游还没有可用结果，请先生成图片或选择一个结果版本")
        if aid not in assets:raise ValueError('引用素材已丢失')
        if assets[aid]['kind']!=SLOTS[card['kind']][e['slot']]:raise ValueError('参考素材类型不匹配：'+e['slot'])
        media_path(assets[aid]);refs[e['slot']]=assets[aid]
    return refs

def compile_prompt(project,card,seed=None):
    validate(project)
    settings=card.get('settings',{});refs=references(project,card)
    if card['kind']=='upscalegen':
        from .video_upscale import compile_upscale
        return compile_upscale(project,card,refs,int(settings.get('seed',42)) if seed is None else seed)
    text=refs.get('prompt',settings.get('prompt','')).strip()
    if not text:raise ValueError('请填写提示词或连接文字卡片')
    width=int(settings.get('width',768));height=int(settings.get('height',768))
    if card['kind']=='videogen':width,height=video_dimensions(settings,refs)
    if width%32 or height%32 or not 256<=width<=1536 or not 256<=height<=1536:raise ValueError('宽高必须为256–1536之间32的倍数')
    seed=int(settings.get('seed',0)) if seed is None else seed
    if seed<0 or seed>9007199254740991:raise ValueError('种子超出安全整数范围')
    steps=int(settings.get('steps',4 if settings.get('engine')=='fibo2' or card['kind']=='videogen' else 8))
    if not 1<=steps<=50:raise ValueError('步数必须为1–50')
    p={};rolemap=[]
    def add(role,cls,**inputs):p[role]={'class_type':cls,'inputs':inputs,'_meta':{'title':role,'studio_role':role}};return [role,0]
    def image(slot,fit='follow'):
        return add('asset_'+slot,'StudioCanvasImage',image=str(media_path(refs[slot])),width=width,height=height,fit=fit)
    prefix=f"Studio/{ident(project['id'])}/{ident(card['id'])}"
    if card['kind']=='imagegen':
        engine=settings.get('engine','qwen');slots=[s for s in ('role1','role2','role3','role4','scene','style') if s in refs]
        if engine=='zimage':
            if slots:raise ValueError('Z-Image不支持角色/场景/风格参考，请选择Qwen 2.1')
            model=add('image_model','UNETLoader',unet_name='z_image_turbo_bf16.safetensors',weight_dtype='default')
            model=add('sampling_shift','ModelSamplingAuraFlow',model=model,shift=3.0)
            clip=add('image_encoder','CLIPLoader',clip_name='qwen_3_4b_fp4_mixed.safetensors',type='lumina2',device='default')
            vae=add('image_vae','VAELoader',vae_name='ae.safetensors')
            pos=add('image_prompt','CLIPTextEncode',clip=clip,text=text)
            neg=add('negative','ConditioningZeroOut',conditioning=pos)
            latent=add('image_canvas','EmptySD3LatentImage',width=width,height=height,batch_size=1)
        elif engine=='qwen':
            model=add('image_model','UNETLoader',unet_name='qwen_image_2.1_turbo_int8_convrot.safetensors',weight_dtype='default')
            clip=add('image_encoder','CLIPLoader',clip_name='qwen3vl_8b_int8_convrot.safetensors',type='qwen_image',device='default')
            vae=add('image_vae','VAELoader',vae_name='qwen_image_2.1_vae_bf16.safetensors')
            encoded={}
            for i,slot in enumerate(slots,1):
                fit=settings.get('fit','follow') if i==1 else 'follow'
                encoded[f'images.image_{i}']=image(slot,fit)
                rolemap.append({'slot':slot,'tag':f'<image{i}>','assetId':refs[slot]['id']})
            guide='\n参考对应：'+', '.join(f"{r['tag']}={r['slot']}" for r in rolemap) if rolemap else ''
            add('image_prompt','TextEncodeQwenImage21',clip=clip,prompt=text+guide,negative_prompt=settings.get('negative',''),vae=vae,resolution=0,**encoded)
            pos=['image_prompt',0];neg=['image_prompt',1]
            if slots:latent=['image_prompt',2]
            else:latent=add('image_canvas','EmptyLatentImage',width=width,height=height,batch_size=1)
        elif engine=='fibo2':
            if len(slots)>4:raise ValueError('FIBO 2 最多连接4张参考图')
            model=add('image_model','UNETLoader',unet_name='fibo2_edit_turbo_int8_convrot.safetensors',weight_dtype='default')
            clip=add('image_encoder','CLIPLoader',clip_name='qwen3vl_4b_int8_convrot.safetensors',type='fibo2',device='default')
            vae=add('image_vae','VAELoader',vae_name='flux2-vae.safetensors')
            encoded={}
            for i,slot in enumerate(slots,1):
                encoded[f'images.image_{i}']=image(slot,settings.get('fit','follow') if i==1 else 'follow')
                rolemap.append({'slot':slot,'tag':f'<image_{i}>','assetId':refs[slot]['id']})
            guide='\nReference mapping: '+', '.join(f"{r['tag']}={r['slot']}" for r in rolemap) if rolemap else ''
            add('image_prompt','TextEncodeFibo2',clip=clip,prompt=text+guide,negative_prompt='',vae=vae,width=width,height=height,batch_size=1,**encoded)
            pos=['image_prompt',0];neg=['image_prompt',1];latent=['image_prompt',2]
        elif engine=='moss':
            if slots:raise ValueError('MOSS 当前仅接入文生图，不支持角色/场景/风格参考，请改用Qwen 2.1')
            model=add('moss_components','MOSSImageHFLoader',model_path=moss_model_path(),weight_dtype='bf16',text_device='cpu')
            clip=['moss_components',1];vae=['moss_components',2]
            model=add('sampling_shift','ModelSamplingSD3',model=model,shift=5.0)
            model=add('moss_slg','SkipLayerGuidanceDiTSimple',model=model,double_layers='',single_layers='22',start_percent=0.0,end_percent=1.0)
            pos=add('image_prompt','CLIPTextEncode',clip=clip,text=text)
            neg=add('negative','CLIPTextEncode',clip=clip,text=settings.get('negative',''))
            latent=add('image_canvas','EmptyFlux2LatentImage',width=width,height=height,batch_size=1)
        else:raise ValueError('未知生图引擎')
        sample=add('image_sampler','KSampler',model=model,seed=seed,steps=steps,cfg=3.0 if engine=='moss' else 1.0,sampler_name='euler' if engine in ('qwen','moss','fibo2') else 'res_multistep',scheduler='sgm_uniform' if engine=='moss' else 'simple',positive=pos,negative=neg,latent_image=latent,denoise=1.0)
        decoded=add('image_decode','VAEDecode',samples=sample,vae=vae)
        add('image_save','SaveImage',images=decoded,filename_prefix=prefix+'/image')
    elif card['kind']=='videogen':
        if settings.get('engine','h3')!='h3':raise ValueError('此视频模型尚未接入，请选择本地H3 Turbo')
        seconds=float(settings.get('duration',3))
        if not 1<=seconds<=15:raise ValueError('首版视频时长为1–15秒；长视频请拆成多个任务')
        if 'scene' in refs or 'style' in refs:raise ValueError('H3首版只支持四角色图，不支持额外场景/风格槽')
        videos=[s for s in ('normal','white','depth') if s in refs]
        if len(videos)>1 and not settings.get('advancedMix',False):raise ValueError('默认只能连接一路视频；请启用高级混合或移除多余连线')
        model=add('video_model','UNETLoader',unet_name='minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors',weight_dtype='default')
        model=add('video_shift','MiniMaxH3SigmaShift',model=model,shift_video=6.0,shift_audio=3.0)
        model=add('video_memory','MiniMaxH3ActivationChunkStar7',model=model,chunk_tokens=4096,auto_halve_on_oom=True,verbose=True,mlp_chunk_tokens=4096,disable_dynamic_prefetch='off',qkv_chunk_tokens=4096,out_proj_chunk_tokens=4096,reuse_mlp_weights=True,attention_backend='comfy_kitchen_int8')
        clip=add('video_encoder','CLIPLoader',clip_name='qwen3vl_32b_heretic_minimax_h3_nvfp4.safetensors',type='minimax',device='default')
        vae=add('video_vae','VAELoader',vae_name='minimax_h3_video_vae_int8_convrot.safetensors')
        audio=add('audio_vae','VAELoader',vae_name='minimax_h3_audio_vae_fp32.safetensors')
        extras={}
        for s in ('first','last'):
            if s in refs:extras['first_frame' if s=='first' else 'last_frame']=image(s,settings.get('fit','pad'))
        for i,s in enumerate(('role1','role2','role3','role4')):
            if s in refs:extras[f'ref_images.ref_image_{i}']=image(s);rolemap.append({'slot':s,'tag':f'<Image {sum(1 for key in extras if key.startswith("ref_images"))}>','assetId':refs[s]['id']})
        for i,s in enumerate(videos):
            v=add('asset_'+s,'StudioCanvasVideo',video=str(media_path(refs[s])),seconds=max(2.3,min(seconds,15)),long_edge=736)
            extras[f'ref_videos.ref_video_{i}']=v
        length=math.ceil(max(0,seconds*24-5)/17)*17+5
        add('video_condition','MiniMaxH3AudioConditioningT8',clip=clip,video_vae=vae,audio_vae=audio,prompt=text,width=width,height=height,length=length,task_type='auto',audio_mode='native',audio_denoise_strength=1.0,add_source_as_reference=False,prompt_primary_audio_ordinal=0,strict_prompt_tags=False,ref_image_size='match',reference_video_policy='official_2_to_15s',allow_above_reference_area=False,**extras)
        noise=add('video_noise','RandomNoise',noise_seed=seed)
        guider=add('video_guider','BasicGuider',model=model,conditioning=['video_condition',0])
        sampler=add('video_sampler_select','KSamplerSelect',sampler_name='euler')
        sigmas=add('video_schedule','BasicScheduler',model=model,scheduler='simple',steps=steps,denoise=1.0)
        sample=add('video_sample','SamplerCustomAdvanced',noise=noise,guider=guider,sampler=sampler,sigmas=sigmas,latent_image=['video_condition',1])
        frames=add('video_decode','VAEDecode',samples=sample,vae=vae)
        args={}
        if settings.get('sound',True):args['audio']=add('audio_decode','VAEDecodeAudio',samples=sample,vae=audio)
        add('video_save','VHS_VideoCombine',images=frames,frame_rate=24.0,loop_count=0,filename_prefix=prefix+'/video',format='video/h264-mp4',pix_fmt='yuv420p',crf=20,save_metadata=True,trim_to_audio=False,pingpong=False,save_output=True,**args)
    else:raise ValueError('只支持生成卡片运行')
    return p,rolemap

def workflow(prompt):
    """Export a native graph using semantic node roles, never persistent numeric IDs."""
    import nodes
    ids={key:i+1 for i,key in enumerate(prompt)};out=[];links=[];lid=0
    for index,(role,node) in enumerate(prompt.items()):
        cls=nodes.NODE_CLASS_MAPPINGS[node['class_type']]
        schema=cls.INPUT_TYPES();allinputs={**schema.get('required',{}),**schema.get('optional',{})}
        inputs=[];values=[]
        for name,value in node['inputs'].items():
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in ids:
                lid+=1;inputs.append({'name':name,'type':allinputs.get(name,['*'])[0],'link':lid});links.append([lid,ids[value[0]],value[1],ids[role],len(inputs)-1,allinputs.get(name,['*'])[0]])
        for name,spec in allinputs.items():
            if name in node['inputs'] and not isinstance(node['inputs'][name],list):
                if isinstance(spec[0],list) or spec[0] in ('INT','FLOAT','BOOLEAN','STRING','COMBO'):
                    values.append(node['inputs'][name])
                    if len(spec)>1 and spec[1].get('control_after_generate'):values.append('fixed')
        outputs=[{'name':str(t),'type':str(t),'links':[]} for t in cls.RETURN_TYPES]
        out.append({'id':ids[role],'type':node['class_type'],'title':role,'pos':[60+(index%4)*400,80+(index//4)*340],'size':[340,260],'flags':{},'order':index,'mode':0,'inputs':inputs,'outputs':outputs,'properties':{'Node name for S&R':node['class_type'],'studio_role':role},'widgets_values':values})
    for link in links:
        n=next(n for n in out if n['id']==link[1]);n['outputs'][link[2]]['links'].append(link[0])
    return {'version':0.4,'nodes':out,'links':links,'groups':[],'last_node_id':len(out),'last_link_id':lid,'extra':{'studio':True}}

def response_error(error):return web.json_response({'error':str(error)},status=400)

@ROUTES.get('/simple-studio/ui')
async def ui(request):return web.FileResponse(WEB/'director.html',headers={'Cache-Control':'no-store'})
@ROUTES.get('/simple-studio/static/{name}')
async def static(request):
    name=request.match_info['name']
    if name not in ('director-app.js','director-app.mjs','director.css','studio-refine.mjs','studio-refine.css','timeline.mjs','editor.mjs','editor.css','editor.html'):raise web.HTTPNotFound()
    return web.FileResponse(WEB/('director-app.mjs' if name=='director-app.js' else name),headers={'Cache-Control':'no-store','Content-Type':'text/javascript' if name.endswith(('.js','.mjs')) else 'text/html' if name.endswith('.html') else 'text/css'})
@ROUTES.get('/simple-studio/projects')
async def list_projects(request):
    items=[]
    for path in ROOT.glob('*/project.json'):
        try:
            p=json.loads(path.read_text(encoding='utf-8'));items.append({k:p.get(k) for k in ('id','name','updated')})
        except (ValueError,OSError):pass
    return web.json_response(sorted(items,key=lambda p:p.get('updated',0),reverse=True))
@ROUTES.post('/simple-studio/projects')
async def create(request):
    data=await request.json();p={'id':uid(),'name':str(data.get('name','新项目'))[:100],'schemaVersion':1,'revision':0,'cards':[],'edges':[],'assets':[],'viewport':{'x':40,'y':40,'zoom':1}}
    write(p);return web.json_response(p)
@ROUTES.get('/simple-studio/projects/{pid}')
async def get_project(request):
    try:return web.json_response(read(request.match_info['pid']))
    except Exception as e:return response_error(e)
@ROUTES.put('/simple-studio/projects/{pid}')
async def put_project(request):
    async with LOCK:
        try:
            pid=request.match_info['pid'];old=read(pid);p=await request.json()
            require_editor(request,pid)
            if p.get('id')!=pid or p.get('schemaVersion')!=1:raise ValueError('项目格式不正确')
            if p.get('revision')!=old['revision']:return web.json_response({'error':'项目已更新，请刷新后重试'},status=409)
            validate(p);p['assets']=old['assets'];p['revision']+=1;write(p);return web.json_response(p)
        except Exception as e:return response_error(e)
async def store_uploaded_asset(request,pid):
    reader=await request.multipart();field=await reader.next()
    if field is None or not field.filename:raise ValueError('没有上传文件')
    name=Path(field.filename).name;ext=Path(name).suffix.lower()
    if ext not in {'.png','.jpg','.jpeg','.webp','.bmp','.mp4','.mov','.webm','.mkv','.wav','.mp3','.flac','.ogg','.m4a','.aac'}:raise ValueError('支持图片、视频和音频素材')
    dest=directory(pid)/'assets';dest.mkdir(parents=True,exist_ok=True);path=dest/(uid()+ext)
    size=0
    try:
        with path.open('wb') as f:
            while chunk:=await field.read_chunk(1024*1024):
                size+=len(chunk)
                if size>2*1024**3:raise ValueError('素材不能超过2GB')
                f.write(chunk)
        a=asset_record(pid,path,name)
    except Exception:
        path.unlink(missing_ok=True);raise
    async with LOCK:
        p=read(pid);p['assets'].append(a);write(p)
    return a

@ROUTES.post('/simple-studio/projects/{pid}/upload')
async def upload(request):
    try:
        pid=request.match_info['pid'];require_editor(request,pid)
        return web.json_response(await store_uploaded_asset(request,pid))
    except Exception as e:return response_error(e)
@ROUTES.get('/simple-studio/projects/{pid}/media/{aid}')
async def media(request):
    try:
        p=read(request.match_info['pid']);a=next(a for a in p['assets'] if a['id']==request.match_info['aid'])
        return web.FileResponse(media_path(a))
    except Exception:raise web.HTTPNotFound()

@ROUTES.post('/simple-studio/projects/{pid}/compile')
async def compile_route(request):
    try:
        pid=request.match_info['pid'];data=await request.json();p=read(pid);card=next(c for c in p['cards'] if c['id']==data['cardId']);prompt,mapping=compile_prompt(p,card)
        return web.json_response({'prompt':prompt,'workflow':workflow(prompt),'mapping':mapping})
    except Exception as e:return response_error(e)
@ROUTES.post('/simple-studio/projects/{pid}/run')
async def run(request):
    import execution
    from .prompt_optimizer import LOCK as OPTIMIZER_LOCK
    if OPTIMIZER_LOCK.locked():
        return web.json_response({'error':'提示词正在优化，请完成后再生成，避免显存冲突'},status=409)
    async with LOCK:
        try:
            data=await request.json();pid=request.match_info['pid'];require_editor(request,pid);p=read(pid)
            if data.get('retryJobId'):
                previous=next(j for j in jobs(pid) if j['id']==data['retryJobId'])
                p=copy.deepcopy(previous['projectSnapshot'])
                data['cardId']=previous['cardId']
            card=next(c for c in p['cards'] if c['id']==data['cardId'])
            count=int(data.get('count',1))
            if count not in (1,2,4) or card['kind'] in ('videogen','upscalegen') and count!=1:raise ValueError('生图支持1、2、4张候选；视频每次生成1个')
            prior=jobs(pid)
            if any(j['cardId']==card['id'] and j['state'] in ('queued','running') for j in prior):raise ValueError('该卡片已有任务，请等待完成')
            prepared=[]
            for i in range(count):
                prompt,mapping=compile_prompt(p,card,int(card.get('settings',{}).get('seed',0))+i)
                prompt_id=uid();PromptServer.instance.node_replace_manager.apply_replacements(prompt)
                valid=await execution.validate_prompt(prompt_id,prompt,None)
                if not valid[0]:
                    details=[]
                    for role,error in valid[3].items():
                        details.extend(role+': '+e.get('message','')+' '+str(e.get('details','')) for e in error.get('errors',[]))
                    raise ValueError('\n'.join(details) or str(valid[1]))
                j={'id':prompt_id,'cardId':card['id'],'state':'queued','created':time.time(),'snapshot':copy.deepcopy(card),'projectSnapshot':copy.deepcopy(p),'mapping':mapping,'prompt':prompt,'assets':[],'candidate':i+1,'clientId':data.get('clientId','')}
                prepared.append((j,valid))
            for j,valid in prepared:
                server=PromptServer.instance;number=server.number;server.number+=1
                server.prompt_queue.put((number,j['id'],j['prompt'],{'client_id':j['clientId'],'studio_project':pid,'create_time':int(time.time()*1000)},valid[2],{}));prior.append(j)
            savejobs(pid,prior);return web.json_response([j for j,_ in prepared])
        except Exception as e:return response_error(e)

def reconcile(pid):
    records=jobs(pid);server=PromptServer.instance;running,pending=server.prompt_queue.get_current_queue();active={q[1]:'running' for q in running};active.update({q[1]:'queued' for q in pending});p=read(pid);changed=False
    for j in records:
        if j['state'] not in ('queued','running'):continue
        if j['id'] in active:
            j['state']=active[j['id']]
            if j['state']=='running' and 'started' not in j:j['started']=time.time()
            continue
        history=server.prompt_queue.get_history(j['id']).get(j['id'])
        if not history:j['state']='interrupted';j['error']='任务不在后台队列，可能已重启或被移除；不会自动重新提交';changed=True;continue
        status=history.get('status',{});j['finished']=time.time();j['elapsed']=j['finished']-j.get('started',j['created']);j['state']='done' if status.get('status_str')=='success' else 'error'
        if j['state']=='error':
            j['errorDetails']=status.get('messages',[])
            errors=[m[1] for m in j['errorDetails'] if m[0] in ('execution_error','execution_interrupted')]
            j['error']='\n'.join(str(e.get('node_id',''))+': '+str(e.get('exception_message','任务已中断')) for e in errors) or '后台任务失败，请查看ComfyUI日志'
        if j['state']=='done':
            for output in history.get('outputs',{}).values():
                for key in ('images','gifs','videos'):
                    for item in output.get(key,[]):
                        if not isinstance(item,dict) or not item.get('filename'):continue
                        base=Path(os.path.abspath(folder_paths.get_output_directory() if item.get('type','output')=='output' else folder_paths.get_temp_directory()));src=Path(os.path.abspath(base/item.get('subfolder','')/item['filename']))
                        if not src.is_relative_to(base) or not src.is_file() or src.suffix.lower() not in {'.png','.jpg','.webp','.mp4','.webm','.mov'}:continue
                        dest=directory(pid)/'assets';dest.mkdir(parents=True,exist_ok=True);new=dest/(uid()+src.suffix)
                        shutil.copy2(src,new);a=asset_record(pid,new,src.name);a.update(jobId=j['id'],cardId=j['cardId'],sourceOutput=str(src));p['assets'].append(a);j['assets'].append(a['id'])
        changed=True
    if changed:write(p)
    savejobs(pid,records);return records
@ROUTES.get('/simple-studio/projects/{pid}/jobs')
async def get_jobs(request):
    async with LOCK:
        try:return web.json_response([{k:v for k,v in j.items() if k not in ('projectSnapshot','prompt')} for j in reconcile(request.match_info['pid'])])
        except Exception as e:return response_error(e)
@ROUTES.post('/simple-studio/projects/{pid}/cancel')
async def cancel(request):
    async with LOCK:
        try:
            pid=request.match_info['pid'];data=await request.json();records=jobs(pid);job=next(j for j in records if j['id']==data['jobId']);server=PromptServer.instance;running,pending=server.prompt_queue.get_current_queue()
            if job['id'] in {q[1] for q in running}:
                if not data.get('confirmInterrupt'):raise ValueError('这会中断当前正在执行的生成，需要明确确认')
                import comfy.model_management
                comfy.model_management.interrupt_current_processing(True)
            else:server.prompt_queue.delete_queue_item(lambda q:q[1]==job['id'])
            job['state']='cancelled';savejobs(pid,records);return web.json_response({'ok':True})
        except Exception as e:return response_error(e)
