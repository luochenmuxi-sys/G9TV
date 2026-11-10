import asyncio
import subprocess
import imageio_ffmpeg
from aiohttp import web
from . import director as d

def extract(source, target):
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    common = ['-map','0:v:0','-an','-vsync','0','-update','1',str(target)]
    for seek in (['-sseof','-3'], []):
        result = subprocess.run([ff,'-hide_banner','-loglevel','error','-y',*seek,'-i',str(source),*common],
                                capture_output=True,timeout=120)
        if result.returncode == 0 and target.is_file():
            return
    raise ValueError('视频末帧解码失败，请检查视频是否能正常播放')

@d.ROUTES.post('/simple-studio/projects/{pid}/tail-frame')
async def tail_frame(request):
    path = None
    try:
        data = await request.json()
        pid = request.match_info['pid']
        d.require_editor(request,pid)
        async with d.LOCK:
            p = d.read(pid)
            src = next(c for c in p['cards'] if c['id']==data['sourceCardId'])
            aid = src.get('assetId') or src.get('selectedAssetId')
            a = next(a for a in p['assets'] if a['id']==aid)
            if a['kind']!='video':
                raise ValueError('请选择已有视频结果的卡片')
            source = d.media_path(a)
        dest = d.directory(pid)/'assets'
        dest.mkdir(exist_ok=True)
        path = dest/(d.uid()+'.png')
        await asyncio.to_thread(extract,source,path)
        async with d.LOCK:
            d.require_editor(request,pid)
            p = d.read(pid)
            if not any(c['id']==src['id'] for c in p['cards']):
                raise ValueError('源视频卡片已删除')
            result = d.asset_record(pid,path,'尾帧_'+a['name']+'.png')
            c = {'id':d.uid(),'kind':'image','title':'尾帧截取','x':float(data.get('x',src['x']+380)),
                 'y':float(data.get('y',src['y'])),'settings':{},'text':'','assetId':result['id'],
                 'extraction':{'type':'tail','sourceAssetId':aid}}
            p['assets'].append(result);p['cards'].append(c)
            p['edges'].append({'id':d.uid(),'from':src['id'],'to':c['id'],'slot':'source_video'})
            d.validate(p);d.write(p)
            return web.json_response({'cardId':c['id'],'asset':result})
    except Exception as exc:
        if path:
            path.unlink(missing_ok=True)
        return d.response_error(exc)
