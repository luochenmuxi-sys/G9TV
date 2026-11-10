"""Local sound workspace, using the native Comfy queue and immutable job snapshots."""
import asyncio, copy, json, re, time, uuid
from pathlib import Path
from aiohttp import web
import folder_paths, nodes, execution
from server import PromptServer
from .director import ROOT, atomic

DATA=ROOT/'_sound'; DATA.mkdir(exist_ok=True)
LOCK=asyncio.Lock(); ROUTES=PromptServer.instance.routes
WEB=Path(__file__).parent/'web'

def read(name,default):
    p=DATA/(name+'.json')
    return json.loads(p.read_text(encoding='utf8')) if p.exists() else default
def save(name,data):atomic(DATA/(name+'.json'),data)
def identifier(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',str(value)):raise ValueError('无效标识')
    return str(value)
def text(value,limit=1500):
    value=str(value).strip()
    if not value or len(value)>limit:raise ValueError(f'请输入1–{limit}字')
    return value
def defaults(kind):
    result={}
    for key,spec in nodes.NODE_CLASS_MAPPINGS[kind].INPUT_TYPES().get('required',{}).items():
        if len(spec)>1 and 'default' in spec[1]:result[key]=spec[1]['default']
        elif isinstance(spec[0],list):result[key]=spec[0][0]
    return result
def reference_file(name):
    base=Path(folder_paths.get_input_directory()).resolve();path=(base/str(name)).resolve()
    if not path.is_relative_to(base/'g9-sound') or not path.is_file():raise ValueError('角色参考音频不存在，请重新上传或选用生成结果')
    return path

def compile_job(settings,roles):
    kind=settings.get('kind','speech');content=text(settings.get('text',''))
    seed=int(settings.get('seed',42))
    if not 1<=seed<=2147483647:raise ValueError('种子须为1–2147483647')
    graph={}
    def add(key,cls,**inputs):graph[key]={'class_type':cls,'inputs':inputs};return [key,0]
    if kind=='speech':
        role=next((r for r in roles if r['id']==settings.get('roleId')),None)
        if settings.get('roleId') and not role:raise ValueError('角色已不存在，请重新选择')
        settings['roleSnapshot']=copy.deepcopy(role)
        model=add('model','BreezeTTS2LoadModel',**{**defaults('BreezeTTS2LoadModel'),'model':'int8 hybrid (recommended)','decode_mode':'eager','download_if_missing':False})
        voice_prompt=str(settings.get('voicePrompt','')).strip() or (role or {}).get('description','清晰自然的普通话')
        voice_prompt=text(voice_prompt,500);settings['voicePrompt']=voice_prompt
        instruction='，'.join(filter(None,[voice_prompt,str(settings.get('emotion','自然')),str(settings.get('pace','正常语速'))]))
        cls='BreezeTTS2VoiceDirection' if role and role.get('reference') else 'BreezeTTS2VoiceDesign'
        args={**defaults(cls),'breeze_model':model,'text':content,'instruction':instruction,'seed':seed,'max_new_tokens':min(1500,max(320,len(content)*10))}
        if cls=='BreezeTTS2VoiceDirection':
            reference_file(role['reference']);args.update(reference_audio=add('reference','LoadAudio',audio=role['reference']),reference_text=text(role.get('referenceText','')),stitch_reference='none')
        audio=add('voice',cls,**args)
    elif kind in ('sfx','music'):
        seconds=float(settings.get('seconds',10))
        if not 1<=seconds<=47:raise ValueError('音效/短音乐时长须为1–47秒')
        steps=int(settings.get('steps',50))
        if not 20<=steps<=100:raise ValueError('音效步数须为20–100')
        model=add('model','CheckpointLoaderSimple',ckpt_name='stable_audio_open_1.0.safetensors')
        clip=add('clip','CLIPLoader',clip_name='t5_base.safetensors',type='stable_audio',device='cpu')
        positive=add('positive','CLIPTextEncode',clip=clip,text=content)
        negative=add('negative','CLIPTextEncode',clip=clip,text='speech, singing, voices, distortion, clipping'+(', music' if kind=='sfx' else ''))
        condition=add('condition','ConditioningStableAudio',positive=positive,negative=negative,seconds_start=0.0,seconds_total=seconds)
        latent=add('latent','EmptyLatentAudio',seconds=seconds,batch_size=1)
        samples=add('sample','KSampler',model=model,positive=condition,negative=['condition',1],latent_image=latent,seed=seed,steps=steps,cfg=5.0,sampler_name='dpmpp_3m_sde_gpu',scheduler='exponential',denoise=1.0)
        audio=add('decode','VAEDecodeAudio',samples=samples,vae=['model',2])
    else:raise ValueError('未知声音类型')
    add('save','SaveAudio',audio=audio,filename_prefix='audio/G9Sound/'+kind)
    return graph

def refresh_jobs():
    records=read('jobs-v2',[]);server=PromptServer.instance
    running,pending=server.prompt_queue.get_current_queue();active={q[1]:'running' for q in running};active.update({q[1]:'queued' for q in pending})
    changed=False
    for j in records:
        if j['state'] not in ('running','queued') or j.get('enginePort')!=PromptServer.instance.port:continue
        state=active.get(j['id'])
        if state:j['state']=state;changed=True;continue
        history=server.prompt_queue.get_history(j['id']).get(j['id'])
        j['finished']=time.time();changed=True
        if not history:j.update(state='interrupted',error='后台已重启或任务被移除，可重新生成');continue
        j['state']='done' if history.get('status',{}).get('status_str')=='success' else 'error'
        j['outputs']=history.get('outputs',{}).get('save',{}).get('audio',[])
        if j['state']=='error':
            errors=[m[1] for m in history.get('status',{}).get('messages',[]) if m[0]=='execution_error']
            j['error']=str(errors[-1].get('exception_message','生成失败'))[-1800:] if errors else '任务已取消或未完成'
    if changed:save('jobs-v2',records)
    return records

@ROUTES.get('/simple-studio/sound/ui')
async def ui(request):return web.FileResponse(WEB/'sound.html',headers={'Cache-Control':'no-store'})
@ROUTES.get('/simple-studio/sound/static/{name}')
async def static(request):
    name=request.match_info['name']
    if name not in ('sound.mjs','sound.css','sound-launcher.mjs'):raise web.HTTPNotFound()
    return web.FileResponse(WEB/name,headers={'Cache-Control':'no-store','Content-Type':'text/css' if name.endswith('.css') else 'text/javascript'})
@ROUTES.get('/simple-studio/sound/state')
async def state(request):
    async with LOCK:
        checkpoints=folder_paths.get_filename_list('checkpoints');encoders=folder_paths.get_filename_list('text_encoders')
        return web.json_response({'roles':read('roles',[]),'jobs':refresh_jobs(),'apiVersion':2,'available':{'speech':all(k in nodes.NODE_CLASS_MAPPINGS for k in ('BreezeTTS2LoadModel','BreezeTTS2VoiceDesign','BreezeTTS2VoiceDirection')),'sfx':'stable_audio_open_1.0.safetensors' in checkpoints and 't5_base.safetensors' in encoders}})
@ROUTES.post('/simple-studio/sound/roles')
async def roles(request):
    try:
        async with LOCK:
            d=await request.json();items=read('roles',[]);rid=identifier(d.get('id') or uuid.uuid4().hex)
            old=next((r for r in items if r['id']==rid),{})
            item={'id':rid,'name':text(d.get('name',''),40),'description':text(d.get('description',''),500),'reference':str(d.get('reference',old.get('reference',''))),'referenceText':str(d.get('referenceText',old.get('referenceText','')))[:1500]}
            if item['reference']:reference_file(item['reference']);text(item['referenceText'])
            if any(r['name']==item['name'] and r['id']!=rid for r in items):raise ValueError('角色名已存在')
            save('roles',[r for r in items if r['id']!=rid]+[item]);return web.json_response(item)
    except (ValueError,KeyError) as e:raise web.HTTPBadRequest(text=str(e))
@ROUTES.post('/simple-studio/sound/reference')
async def upload_reference(request):
    try:
        import soundfile as sf
        reader=await request.multipart();field=await reader.next();ext=Path(field.filename or '').suffix.lower()
        if ext not in ('.wav','.flac','.mp3','.ogg'):raise ValueError('请上传WAV、FLAC、MP3或OGG')
        data=bytearray()
        while chunk:=await field.read_chunk():
            data.extend(chunk)
            if len(data)>20*1024*1024:raise ValueError('参考音频不能超过20MB')
        import io
        with sf.SoundFile(io.BytesIO(data)) as f:
            seconds=len(f)/f.samplerate
            if not 1<=seconds<=30:raise ValueError('参考音频须为1–30秒的清晰单人讲话')
        relative='g9-sound/'+uuid.uuid4().hex+ext;path=Path(folder_paths.get_input_directory())/relative;path.parent.mkdir(exist_ok=True);path.write_bytes(data)
        return web.json_response({'reference':relative,'seconds':seconds})
    except Exception as e:raise web.HTTPBadRequest(text=str(e))
@ROUTES.post('/simple-studio/sound/generate')
async def generate(request):
    try:
        async with LOCK:
            d=await request.json();roles=read('roles',[]);records=refresh_jobs()
            if len([j for j in records if j['state'] in ('running','queued')])>40:raise ValueError('请等待当前任务完成')
            settings=d.get('items',[d])
            if not isinstance(settings,list) or not 1<=len(settings)<=30:raise ValueError('一次最多提交30句')
            prepared=[]
            for item in settings:
                snapshot=copy.deepcopy(item);graph=compile_job(snapshot,roles);jid=uuid.uuid4().hex
                PromptServer.instance.node_replace_manager.apply_replacements(graph)
                valid=await execution.validate_prompt(jid,graph,None)
                if not valid[0]:raise ValueError('模型或参数检查失败：'+json.dumps(valid[3] or valid[1],ensure_ascii=False))
                j={'id':jid,'enginePort':PromptServer.instance.port,'state':'queued','created':time.time(),'settings':snapshot,'outputs':[],'parentId':str(item.get('parentId',''))}
                prepared.append((j,graph,valid[2]))
            for j,graph,outputs in prepared:
                server=PromptServer.instance;number=server.number;server.number+=1
                server.prompt_queue.put((number,j['id'],graph,{'client_id':'g9-sound','create_time':int(time.time()*1000)},outputs,{}));records.append(j)
            save('jobs-v2',records);return web.json_response([j for j,_,_ in prepared])
    except Exception as e:raise web.HTTPBadRequest(text=str(e))
@ROUTES.post('/simple-studio/sound/cancel')
async def cancel(request):
    async with LOCK:
        d=await request.json();jid=identifier(d.get('id',''));records=refresh_jobs();j=next((j for j in records if j['id']==jid),None)
        if not j:raise web.HTTPNotFound()
        if j.get('enginePort')!=PromptServer.instance.port:raise web.HTTPConflict(text='该任务由另一个服务处理，请回到提交任务的页面操作')
        running,pending=PromptServer.instance.prompt_queue.get_current_queue()
        if any(q[1]==jid for q in running):
            import comfy.model_management
            comfy.model_management.interrupt_current_processing(True)
        else:PromptServer.instance.prompt_queue.delete_queue_item(lambda q:q[1]==jid)
        j['state']='cancelled';save('jobs-v2',records);return web.json_response({'ok':True})
@ROUTES.post('/simple-studio/sound/anchor')
async def anchor(request):
    try:
        import shutil
        async with LOCK:
            d=await request.json();job=next(j for j in refresh_jobs() if j['id']==d['jobId']);role=next(r for r in read('roles',[]) if r['id']==d['roleId'])
            if job['state']!='done' or job['settings']['kind']!='speech' or not job['outputs']:raise ValueError('只能选用成功的人声结果')
            out=job['outputs'][0];base=Path(folder_paths.get_output_directory()).resolve();src=(base/out.get('subfolder','')/out['filename']).resolve()
            if not src.is_relative_to(base) or not src.is_file():raise ValueError('音频文件不存在')
            import soundfile as sf
            with sf.SoundFile(src) as f:
                if not 1<=len(f)/f.samplerate<=30:raise ValueError('请选择1–30秒的人声样本')
            rel='g9-sound/'+uuid.uuid4().hex+src.suffix;dest=Path(folder_paths.get_input_directory())/rel;dest.parent.mkdir(exist_ok=True);shutil.copy2(src,dest)
            role.update(reference=rel,referenceText=job['settings']['text']);items=read('roles',[]);save('roles',[role if r['id']==role['id'] else r for r in items]);return web.json_response(role)
    except Exception as e:raise web.HTTPBadRequest(text=str(e))
