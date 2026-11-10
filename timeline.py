"""Project-scoped, CPU-only timeline exports."""
import asyncio
import datetime
import re
import subprocess
import zipfile
import imageio_ffmpeg
from aiohttp import web
from . import director as d

EXPORTS = {}
EXPORT_LOCK = asyncio.Lock()

def build_export(items, folder, mode, state):
    folder.mkdir(parents=True, exist_ok=True)
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    if mode == 'separate':
        target = folder/'timeline.zip'
        with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for i, a in enumerate(items):
                stamp = datetime.datetime.fromtimestamp(float(a.get('created', 0)), datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')
                path = d.media_path(a)
                archive.write(path, f'{i+1:03d}_{stamp}{path.suffix}')
                state['progress'] = round((i+1)/len(items)*100)
        return target
    clips = []
    for i, a in enumerate(items):
        source = d.media_path(a)
        probe = subprocess.run([ff, '-hide_banner', '-i', str(source)], capture_output=True, timeout=30).stderr.decode(errors='replace')
        audio = bool(re.search(r'Stream .*Audio:', probe))
        target = folder/f'{i:03d}.mp4'
        args = [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source)]
        if not audio:
            args += ['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
        args += ['-map','0:v:0','-map','0:a:0' if audio else '1:a:0',
                 '-vf','scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24',
                 '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p','-c:a','aac','-ar','48000','-ac','2','-af','apad','-shortest',str(target)]
        result = subprocess.run(args, capture_output=True, timeout=7200)
        if result.returncode:
            raise ValueError('片段转码失败：'+result.stderr.decode(errors='replace')[-600:])
        clips.append(target)
        state['progress'] = round((i+1)/len(items)*90)
    listing = folder/'clips.txt'
    listing.write_text('\n'.join(f"file '{p.name}'" for p in clips), encoding='utf-8')
    target = folder/'timeline.mp4'
    result = subprocess.run([ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(listing),'-c','copy','-movflags','+faststart',str(target)], capture_output=True, timeout=7200)
    if result.returncode:
        raise ValueError('合并失败：'+result.stderr.decode(errors='replace')[-600:])
    for p in clips:
        p.unlink(missing_ok=True)
    listing.unlink(missing_ok=True)
    return target

async def export_job(key, items, folder, mode):
    state = EXPORTS[key]
    try:
        async with EXPORT_LOCK:
            state['status'] = 'running'
            path = await asyncio.to_thread(build_export, items, folder, mode, state)
            state.update(status='complete', progress=100, path=str(path))
    except Exception as e:
        state.update(status='failed', error=str(e))

@d.ROUTES.post('/simple-studio/projects/{pid}/timeline-export')
async def start(request):
    try:
        pid = request.match_info['pid']
        d.require_editor(request, pid)
        if any(s['pid']==pid and s['status'] in ('queued','running') for s in EXPORTS.values()):
            raise ValueError('当前项目已有导出任务，请等待完成')
        data = await request.json()
        mode = data.get('mode')
        if mode not in ('merged','separate'):
            raise ValueError('无效导出方式')
        ids = data.get('assets', [])
        if not isinstance(ids,list) or not 1 <= len(ids) <= 100:
            raise ValueError('请选择 1–100 个视频片段')
        project = d.read(pid)
        lookup = {a['id']: a for a in project['assets'] if a['kind']=='video'}
        items = [lookup[aid] for aid in ids]
        for a in items:
            d.media_path(a)
        key = d.uid()
        EXPORTS[key] = dict(pid=pid, status='queued', progress=0)
        asyncio.create_task(export_job(key, items, d.directory(pid)/'timeline_exports'/key, mode))
        return web.json_response({'id':key})
    except Exception as e:
        return d.response_error(e)

@d.ROUTES.get('/simple-studio/projects/{pid}/timeline-export/{eid}')
async def status(request):
    state = EXPORTS.get(request.match_info['eid'])
    if not state or state['pid'] != request.match_info['pid']:
        raise web.HTTPNotFound(text='导出任务不存在，服务重启后请重新导出')
    return web.json_response({k:v for k,v in state.items() if k != 'path'})

@d.ROUTES.get('/simple-studio/projects/{pid}/timeline-export/{eid}/download')
async def download(request):
    state = EXPORTS.get(request.match_info['eid'])
    if not state or state['pid'] != request.match_info['pid'] or state['status'] != 'complete':
        raise web.HTTPNotFound()
    return web.FileResponse(state['path'], headers={'Content-Disposition':'attachment; filename="'+('G9TV_timeline.zip' if state['path'].endswith('.zip') else 'G9TV_timeline.mp4')+'"'})
