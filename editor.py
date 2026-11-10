"""Project-local editor documents and local recording conversion."""
import asyncio
import json
import math
import subprocess
import os
import time
from urllib.parse import urlencode
from pathlib import Path

from aiohttp import web
import imageio_ffmpeg
from . import director as d

ENCODE = asyncio.Semaphore(1)
WINDOWS = {}


@d.ROUTES.post('/simple-studio/projects/{pid}/editor-window')
async def open_editor_window(request):
    try:
        peer = request.transport.get_extra_info('peername')
        if not peer or peer[0] not in ('127.0.0.1', '::1'):
            raise ValueError('剪辑窗口只能在本机打开')
        pid = request.match_info['pid']
        project = d.read(pid)
        body = await request.json()
        payload = body.get('payload')
        if payload:
            if len(json.dumps(payload)) > 200000 or payload.get('projectId') != pid:
                raise ValueError('素材传递内容不正确')
            known = {a['id'] for a in project['assets']}
            if any(a.get('id') not in known for a in payload.get('assets', [])):
                raise ValueError('素材尚未保存到当前项目')
        now = time.monotonic()
        for key in list(WINDOWS):
            if now - WINDOWS[key]['seen'] > 3600:
                del WINDOWS[key]
        session = WINDOWS.get(pid)
        if not session or now - session['seen'] > 12:
            edge = next((path for path in [
                Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft/Edge/Application/msedge.exe',
                Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Microsoft/Edge/Application/msedge.exe',
            ] if path.is_file()), None)
            if not edge:
                raise ValueError('未找到 Microsoft Edge，请先安装 Edge 后使用独立剪辑窗口')
            session = {'token': d.uid(), 'seen': now, 'events': []}
            port = request.transport.get_extra_info('sockname')[1]
            url = 'http://127.0.0.1:' + str(port) + '/simple-studio/static/editor.html?' + urlencode({'project': pid, 'bridge': session['token']})
            subprocess.Popen([str(edge), '--app=' + url, '--window-size=1440,960'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            WINDOWS[pid] = session
        if payload:
            if len(session['events']) >= 64:
                raise ValueError('待加入素材过多，请等待剪辑窗口处理完成')
            session['events'].append({'id': d.uid(), 'payload': payload})
        return web.json_response({'ok': True})
    except (ValueError, OSError, KeyError) as error:
        return d.response_error(error)


@d.ROUTES.post('/simple-studio/projects/{pid}/editor-window/{token}')
async def poll_editor_window(request):
    session = WINDOWS.get(request.match_info['pid'])
    if not session or session['token'] != request.match_info['token']:
        return web.json_response({'error': '窗口连接已失效，请保存后重新打开剪辑台'}, status=404)
    body = await request.json()
    ack = set(body.get('ack', []))
    session['events'] = [event for event in session['events'] if event['id'] not in ack]
    session['seen'] = time.monotonic()
    return web.json_response({'events': session['events']})


def validate_document(data):
    if data.get('version') != 1 or data.get('size') not in {'1280x720', '1920x1080', '1080x1920', '1080x1080', '3840x2160'}:
        raise ValueError('剪辑项目格式或画幅不正确')
    tracks, sources, clips = data.get('tracks', []), data.get('sources', []), data.get('clips', [])
    if len(tracks) > 100 or len(sources) > 1000 or len(clips) > 3000:
        raise ValueError('剪辑项目过大')
    by_track = {t['id']: t for t in tracks}
    by_source = {s['id']: s for s in sources}
    if len(by_track) != len(tracks) or len(by_source) != len(sources) or len({c['id'] for c in clips}) != len(clips):
        raise ValueError('剪辑项目标识重复')
    for t in tracks:
        if t.get('kind') not in ('video', 'audio'):
            raise ValueError('未知轨道类型')
    for c in clips:
        if c.get('trackId') not in by_track or ('text' not in c and c.get('sourceId') not in by_source):
            raise ValueError('片段引用的素材或轨道不存在')
        for key in ('start', 'in', 'out', 'speed'):
            value = c.get(key, 1 if key == 'speed' else 0)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('片段时间不正确')
        if c['start'] < 0 or c['in'] < 0 or c['out'] <= c['in'] or not .25 <= c.get('speed', 1) <= 4:
            raise ValueError('片段时间或速度超出范围')
    if len(json.dumps(data, ensure_ascii=False)) > 4_000_000:
        raise ValueError('剪辑项目超过4MB')


@d.ROUTES.get('/simple-studio/projects/{pid}/editor')
async def get_document(request):
    try:
        pid = request.match_info['pid']
        d.read(pid)
        path = d.directory(pid) / 'editor.json'
        return web.json_response(json.loads(path.read_text(encoding='utf8')) if path.exists() else {'revision': 0, 'data': None})
    except (ValueError, OSError) as error:
        return d.response_error(error)


@d.ROUTES.put('/simple-studio/projects/{pid}/editor')
async def put_document(request):
    try:
        pid = request.match_info['pid']
        d.require_editor(request, pid + '-editor')
        body = await request.json()
        validate_document(body['data'])
        async with d.LOCK:
            project = d.read(pid)
            registered = {a['id']: a for a in project['assets']}
            for source in body['data']['sources']:
                asset = registered.get(source['id'])
                if not asset or source.get('kind') != asset['kind'] or source.get('url') != f"/simple-studio/projects/{pid}/media/{asset['id']}":
                    raise ValueError('请先把素材导入当前项目，再加入时间线')
            path = d.directory(pid) / 'editor.json'
            old = json.loads(path.read_text(encoding='utf8')) if path.exists() else {'revision': 0}
            if body.get('revision') != old['revision']:
                return web.json_response({'error': '剪辑项目已被另一窗口修改，请重新打开剪辑台'}, status=409)
            result = {'revision': old['revision'] + 1, 'data': body['data']}
            d.atomic(path, result)
        return web.json_response({'revision': result['revision']})
    except (ValueError, KeyError, OSError) as error:
        return d.response_error(error)


def convert_recording(source, target):
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
               '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-r', '30', '-c:v', 'libx264', '-preset', 'fast',
               '-crf', '19', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(target)]
    result = subprocess.run(command, capture_output=True, timeout=1800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        target.unlink(missing_ok=True)
        raise ValueError('MP4编码失败：' + result.stderr.decode('utf8', errors='replace')[-800:])


@d.ROUTES.post('/simple-studio/projects/{pid}/editor-export')
async def export_recording(request):
    source = None
    try:
        pid = request.match_info['pid']
        d.require_editor(request, pid + '-editor')
        d.read(pid)
        reader = await request.multipart()
        field = await reader.next()
        if field is None or not field.filename or Path(field.filename).suffix.lower() != '.webm':
            raise ValueError('请上传剪辑台生成的WebM录制文件')
        root = d.directory(pid) / 'assets'
        root.mkdir(exist_ok=True)
        identifier = d.uid()
        source, target = root / (identifier + '.webm'), root / (identifier + '.mp4')
        size = 0
        with source.open('wb') as stream:
            while chunk := await field.read_chunk(1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024 ** 3:
                    raise ValueError('导出文件超过2GB')
                stream.write(chunk)
        async with ENCODE:
            await asyncio.to_thread(convert_recording, source, target)
        asset = d.asset_record(pid, target, 'G9TV-剪辑导出.mp4')
        async with d.LOCK:
            project = d.read(pid)
            project['assets'].append(asset)
            d.write(project)
        return web.json_response(asset)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        return d.response_error(error)
    finally:
        if source:
            source.unlink(missing_ok=True)


@d.ROUTES.post('/simple-studio/projects/{pid}/editor-upload')
async def import_asset(request):
    try:
        pid = request.match_info['pid']
        d.require_editor(request, pid + '-editor')
        return web.json_response(await d.store_uploaded_asset(request, pid))
    except (ValueError, OSError) as error:
        return d.response_error(error)
