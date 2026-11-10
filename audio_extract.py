"""Extract the first audio track from registered video assets, without GPU inference."""
import asyncio, json, time
from pathlib import Path
from aiohttp import web
from . import director as d

STORE=d.ROOT/'_sound'/'extracted';STORE.mkdir(parents=True,exist_ok=True)
LIMIT=asyncio.Semaphore(1)

def extract_audio(source,target):
    import av
    import numpy as np
    import wave
    frames=0
    try:
        with av.open(str(source)) as container:
            if not container.streams.audio:raise ValueError('这个视频没有音轨，无法提取音频。请使用带声音的视频。')
            stream=container.streams.audio[0]
            resampler=av.AudioResampler(format='s16',layout='stereo',rate=48000)
            with wave.open(str(target),'wb') as output:
                output.setnchannels(2);output.setsampwidth(2);output.setframerate(48000)
                for frame in container.decode(stream):
                    for audio in resampler.resample(frame):
                        frames+=audio.samples
                        if frames>48000*600:raise ValueError('单次最多提取10分钟音频，请先裁剪视频。')
                        output.writeframesraw(audio.to_ndarray().astype('<i2',copy=False).tobytes())
                for audio in resampler.resample(None):
                    frames+=audio.samples;output.writeframesraw(audio.to_ndarray().astype('<i2',copy=False).tobytes())
        if not frames:raise ValueError('视频音轨为空，未提取到有效音频。')
        return {'seconds':round(frames/48000,3),'sampleRate':48000,'channels':2}
    except Exception:
        target.unlink(missing_ok=True);raise

@d.ROUTES.post('/simple-studio/projects/{pid}/extract-audio')
async def extract(request):
    try:
        pid=request.match_info['pid'];d.require_editor(request,pid);data=await request.json()
        async with d.LOCK:
            project=d.read(pid);card=next((c for c in project['cards'] if c['id']==data.get('sourceCardId')),None)
            if not card:raise ValueError('视频卡片不存在，请刷新工作台。')
            aid=card.get('assetId') or card.get('selectedAssetId');asset=next((a for a in project['assets'] if a['id']==aid),None)
            if not asset or asset.get('kind')!='video':raise ValueError('请先生成或导入视频，并选择要提取的版本。')
            source=d.media_path(asset);name=Path(asset.get('name','视频')).stem+'-音频';identifier=d.uid()
        async with LIMIT:metrics=await asyncio.to_thread(extract_audio,source,STORE/(identifier+'.wav'))
        record={'id':identifier,'name':name,'created':time.time(),'projectId':pid,'sourceAssetId':aid,**metrics}
        d.atomic(STORE/(identifier+'.json'),record)
        return web.json_response(record)
    except Exception as e:return d.response_error(e)

@d.ROUTES.get('/simple-studio/extracted-audio')
async def library(request):
    items=[]
    for path in STORE.glob('*.json'):
        try:
            item=json.loads(path.read_text(encoding='utf8'))
            if (STORE/(d.ident(item['id'])+'.wav')).is_file():items.append(item)
        except (OSError,ValueError,KeyError):continue
    return web.json_response(sorted(items,key=lambda r:r['created'],reverse=True)[:200])

@d.ROUTES.get('/simple-studio/extracted-audio/{aid}')
async def media(request):
    identifier=d.ident(request.match_info['aid']);path=STORE/(identifier+'.wav')
    if not path.is_file():raise web.HTTPNotFound()
    response=web.FileResponse(path,headers={'Content-Type':'audio/wav'})
    if request.query.get('download')=='1':response.headers['Content-Disposition']='attachment; filename="G9TV-audio-'+identifier+'.wav"'
    return response
