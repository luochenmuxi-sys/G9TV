"""Local-only H3 rewriting. No model downloads, arbitrary URLs, or generation submission."""
import asyncio
import json
import os
import pathlib
import subprocess
import aiohttp
from aiohttp import web
from server import PromptServer

MODEL = 'qwen3.5:9b'
URL = 'http://127.0.0.1:11439'
ROOT = pathlib.Path(__file__).parent
LOCK = asyncio.Lock()
PROCESS = None

async def ensure_service(session):
    global PROCESS
    try:
        async with session.get(URL+'/api/version', timeout=aiohttp.ClientTimeout(total=2)) as r:
            if r.status == 200:
                return
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass
    exe = pathlib.Path(os.environ.get('G9TV_OLLAMA_EXE', 'D:/ComfyUI/LocalLLM/ollama/ollama.exe'))
    if not exe.is_file():
        raise ValueError('本地语言模型运行程序未安装')
    if PROCESS is None or PROCESS.poll() is not None:
        env = os.environ.copy()
        env.update(OLLAMA_HOST='127.0.0.1:11439', OLLAMA_MODELS=os.environ.get('G9TV_OLLAMA_MODELS', 'D:/ComfyUI/LocalLLM/models'),
                   OLLAMA_NO_CLOUD='1', OLLAMA_MAX_LOADED_MODELS='1')
        PROCESS = subprocess.Popen([str(exe), 'serve'], env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(30):
        await asyncio.sleep(.25)
        try:
            async with session.get(URL+'/api/version', timeout=aiohttp.ClientTimeout(total=2)) as r:
                if r.status == 200:
                    return
        except (aiohttp.ClientError, asyncio.TimeoutError):
            pass
    raise ValueError('本地语言模型服务未启动，请稍后重试')

def system_prompt(mode):
    skill = (ROOT/'prompt-guides/SKILL.md').read_text(encoding='utf-8')
    base = (ROOT/'prompt-guides/base-en.txt').read_text(encoding='utf-8')
    ref = (ROOT/'prompt-guides/ref-en.txt').read_text(encoding='utf-8') if mode == 'Ref2VA' else ''
    # Send normative rules, not unrelated mode examples that a small model may copy.
    base = base.split('## 5. Cases')[0]
    if mode == 'Ref2VA':
        base = '## 4. '+base.split('## 4. ',1)[1]
        ref = ref.split('## 7. Complete Example')[0]
    fields = 'subject_definitions, summary, retention_analysis, detailed_description, overall_soundscape, non_diegetic_music' if mode == 'Ref2VA' else 'integrated_multimodal_description, overall_soundscape, non_diegetic_music'
    return ('You are the local H3 prompt rewriter. Return ONLY the final rewritten prompt, no preface or markdown fences. '
            'Preserve user intent, exact dialogue and visible text. Never invent asset contents: only reference slot roles are supplied, not pixels. '
            'Do not invent reference assets or dialogue. Prefer one continuous shot unless cuts are requested. '
            'Never claim strict depth or motion control: white/depth videos are visual references only. '
            'Do not add music unless requested; otherwise non_diegetic_music: N/A. '
            'When sound_enabled is false, write overall_soundscape: N/A and non_diegetic_music: N/A and no spoken audio. '
            'Fit all actions to the supplied duration. Use the following official skill and format rules.\n'+skill+'\n'+base+'\n'+ref+
            '\nMANDATORY: Current mode is '+mode+'. Return ALL fields as plain field_name: text, in this exact order: '+fields+
            '. No markdown headings, bullets around field names, or code fences. Do not copy examples. '+
            'Only first/last slots are keyframes; role image slots are NOT first/last frames. Never invent a second picture or a final frame. '
            'Use only the supplied asset_mapping labels. Preserve unknown appearance by reference rather than guessing it.')

def validate_result(text, mode):
    fields = ['subject_definitions:', 'summary:', 'retention_analysis:', 'detailed_description:',
              'overall_soundscape:', 'non_diegetic_music:'] if mode == 'Ref2VA' else [
              'integrated_multimodal_description:', 'overall_soundscape:', 'non_diegetic_music:']
    positions = [text.find(f) for f in fields]
    if len(text) < 100 or any(p < 0 for p in positions) or positions != sorted(positions):
        raise ValueError('模型输出格式未通过检查，原文未修改，请重试')
    return text

@PromptServer.instance.routes.get('/simple-studio/prompt-optimize/status')
async def optimizer_status(request):
    import hashlib
    files = []
    for name in ('SKILL.md','base-en.txt','ref-en.txt'):
        path = ROOT/'prompt-guides'/name
        files.append({'name':name,'installed':path.is_file(),
                      'sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None})
    manifest = pathlib.Path(os.environ.get('G9TV_OLLAMA_MODELS', 'D:/ComfyUI/LocalLLM/models'))/'manifests/registry.ollama.ai/library/qwen3.5/9b'
    return web.json_response({'model':MODEL,'model_installed':manifest.is_file(),
                              'skills':files,'busy':LOCK.locked(),'local_only':True})

@PromptServer.instance.routes.post('/simple-studio/prompt-optimize')
async def optimize(request):
    if request.headers.get('Origin') and request.headers['Origin'] != f'{request.scheme}://{request.host}':
        raise web.HTTPForbidden()
    if LOCK.locked():
        return web.json_response({'error':'已有提示词正在优化，请稍候'}, status=409)
    data = await request.json()
    text = data.get('text', '')
    if not isinstance(text, str) or not text.strip() or len(text) > 12000:
        return web.json_response({'error':'请填写1–12000字的提示词'}, status=400)
    slots = data.get('slots', [])
    allowed = ['first','last','role1','role2','role3','role4','normal','white','depth']
    if not isinstance(slots,list) or any(s not in allowed for s in slots):
        return web.json_response({'error':'参考槽位无效'},status=400)
    try:
        duration = float(data.get('duration', 5))
    except (TypeError, ValueError):
        return web.json_response({'error':'时长格式无效'}, status=400)
    if not 1 <= duration <= 15:
        return web.json_response({'error':'时长需为1–15秒'},status=400)
    mode = 'Ref2VA' if any(s not in ('first','last') for s in slots) else 'FL2VA' if 'first' in slots and 'last' in slots else 'I2VA' if 'first' in slots else 'L2VA' if 'last' in slots else 'T2VA'
    image_slots = [s for s in ['first','last','role1','role2','role3','role4'] if s in slots]
    video_slots = [s for s in ['normal','white','depth'] if s in slots]
    mapping = [{'label':f'<Picture {i}>','slot':s,'keyframe':s in ('first','last')} for i,s in enumerate(image_slots,1)]
    mapping += [{'label':f'<Video {i}>','slot':s,'role':'visual reference only'} for i,s in enumerate(video_slots,1)]
    from .director import LOCK as GENERATION_LOCK
    async with LOCK, GENERATION_LOCK:
        running, pending = PromptServer.instance.prompt_queue.get_current_queue()
        if running or pending:
            return web.json_response({'error':'请等待图片/视频生成队列完成，再优化提示词，避免争抢显存'}, status=409)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=240), trust_env=False) as session:
            try:
                await ensure_service(session)
                payload = {'model':MODEL,'stream':False,'think':False,'keep_alive':0,
                           'options':{'num_ctx':16384,'num_predict':2300,'temperature':0.35},
                           'messages':[{'role':'system','content':system_prompt(mode)},
                                       {'role':'user','content':json.dumps({'mode':mode,'duration_seconds':duration,
                                        'reference_slots':slots,'asset_mapping':mapping,'sound_enabled':bool(data.get('sound',True)),
                                        'instruction':text},ensure_ascii=False)}]}
                async with session.post(URL+'/api/chat',json=payload) as response:
                    result = await response.json()
                    if response.status != 200:
                        raise ValueError('本地模型调用失败：'+str(result.get('error','未知错误'))[:200])
                rewritten = validate_result(result.get('message',{}).get('content','').strip(),mode)
                return web.json_response({'text':rewritten,'model':MODEL,'mode':mode,'local':True,
                                          'note':'只读取提示词与槽位信息，未读取图片或视频内容'})
            except (ValueError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                return web.json_response({'error':str(exc) or '本地模型响应超时，原文未修改'},status=503)
            finally:
                try:
                    async with session.post(URL+'/api/generate',json={'model':MODEL,'keep_alive':0},
                                            timeout=aiohttp.ClientTimeout(total=15)) as response:
                        await response.read()
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    pass
