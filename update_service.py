"""Local UI endpoints for explicitly requested GitHub updates."""
import asyncio
import threading
from aiohttp import web
from server import PromptServer
from . import director as d
from . import g9_update

LOCK = threading.Lock()


def local_request(request):
    peer = request.transport.get_extra_info('peername')
    origin = request.headers.get('Origin')
    if not peer or peer[0] not in ('127.0.0.1', '::1') or (origin and origin != f'{request.scheme}://{request.host}'):
        raise ValueError('更新操作只允许从本机工作台发起')


@d.ROUTES.get('/simple-studio/update/check')
async def check(request):
    try:
        local_request(request)
        info = await asyncio.to_thread(g9_update.manifest)
        current = g9_update.current_version()
        return web.json_response({'current': current, 'latest': info['version'], 'available': g9_update.newer(info['version'], current), 'notes': info.get('notes', '')})
    except Exception as error:
        return d.response_error(error)


@d.ROUTES.post('/simple-studio/update/apply')
async def apply(request):
    acquired = False
    try:
        local_request(request)
        if (await request.json()).get('install') is not True:
            raise ValueError('请在更新面板点击下载并安装')
        running, pending = PromptServer.instance.prompt_queue.get_current_queue()
        if running or pending:
            raise ValueError('生成队列仍有任务，请等待全部完成后更新')
        acquired = LOCK.acquire(blocking=False)
        if not acquired:
            raise ValueError('更新正在进行，请等待完成')
        return web.json_response(await asyncio.to_thread(g9_update.apply_update))
    except Exception as error:
        return d.response_error(error)
    finally:
        if acquired:
            LOCK.release()
