"""Export only registered current-version media; never accepts filesystem paths."""
import asyncio
import datetime
import os
import tempfile
import zipfile
from pathlib import Path
from aiohttp import web
from server import PromptServer
from .director import read, jobs, media_path, ident

LIMIT = asyncio.Semaphore(2)
TZ = datetime.timezone(datetime.timedelta(hours=8))

def export_items(project, card_ids, records):
    by = {c['id']: c for c in project['cards']}
    assets = {a['id']: a for a in project['assets']}
    times = {aid:j.get('finished',j.get('created')) for j in records for aid in j.get('assets',[])}
    chosen = {}
    for cid in card_ids:
        if cid not in by:
            raise ValueError('所选卡片已不存在，请刷新')
        c = by[cid]
        aid = c.get('assetId') if c['kind'] in ('image','video') else c.get('selectedAssetId')
        if aid and aid in assets:
            a = assets[aid]
            if a.get('kind') in ('image','video'):
                path = media_path(a)
                chosen[aid] = (float(times.get(aid) or a.get('created') or path.stat().st_mtime), aid, path)
    if not chosen:
        raise ValueError('所选卡片没有可下载的图片或视频')
    result = []
    used = set()
    for timestamp, aid, path in sorted(chosen.values()):
        dt = datetime.datetime.fromtimestamp(timestamp,TZ)
        stem = dt.strftime('%Y%m%d_%H%M%S')+f'_{dt.microsecond//1000:03d}'
        ext = path.suffix.lower()
        name = stem+ext
        n = 2
        while name in used:
            name = f'{stem}_{n:02d}{ext}'
            n += 1
        used.add(name)
        result.append((path,name))
    return result

@PromptServer.instance.routes.get('/simple-studio/projects/{pid}/download')
async def download_selected(request):
    try:
        pid = ident(request.match_info['pid'])
        ids = list(dict.fromkeys(request.query.get('cards','').split(',')))
        if not ids or len(ids)>500 or any(not cid for cid in ids):
            raise ValueError('请选择1–500张卡片')
        for cid in ids:
            ident(cid)
        items = export_items(read(pid),ids,jobs(pid))
    except (ValueError,KeyError,FileNotFoundError) as exc:
        return web.json_response({'error':str(exc)},status=400)
    async with LIMIT:
        fd,name = tempfile.mkstemp(prefix='g9tv-export-',suffix='.zip')
        os.close(fd)
        try:
            def pack():
                with zipfile.ZipFile(name,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
                    for path,entry in items:
                        archive.write(path,entry)
            await asyncio.to_thread(pack)
            stamp = datetime.datetime.now(TZ).strftime('%Y%m%d_%H%M%S')
            response = web.StreamResponse(headers={'Content-Type':'application/zip',
                'Content-Disposition':f'attachment; filename="G9TV_{stamp}.zip"',
                'Content-Length':str(Path(name).stat().st_size),'Cache-Control':'no-store'})
            await response.prepare(request)
            with open(name,'rb') as source:
                while chunk := await asyncio.to_thread(source.read,1024*1024):
                    await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            Path(name).unlink(missing_ok=True)
