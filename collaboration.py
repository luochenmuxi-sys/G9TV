"""Short-lived project editing leases shared by local and remote browsers."""
import re
import time
from aiohttp import web
from server import PromptServer

LEASES = {}

def actor(request):
    value = request.headers.get('X-Studio-Client', '')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{16,80}', value):
        raise web.HTTPConflict(text='请刷新导演台以加载协作客户端', content_type='text/plain')
    return value

def require_editor(request, pid):
    who = actor(request)
    lease = LEASES.get(pid)
    if lease and lease['expires'] > time.monotonic() and lease['owner'] != who:
        raise web.HTTPConflict(text='其他用户正在编辑，请等待对方交出编辑权')
    LEASES[pid] = {'owner': who, 'expires': time.monotonic()+30}

@PromptServer.instance.routes.post('/simple-studio/collaboration/{pid}')
async def collaboration(request):
    pid = request.match_info['pid']
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',pid):
        raise web.HTTPBadRequest()
    who = actor(request)
    data = await request.json()
    lease = LEASES.get(pid)
    if lease and lease['expires'] <= time.monotonic():
        LEASES.pop(pid,None)
        lease = None
    if data.get('action') == 'release' and lease and lease['owner'] == who:
        LEASES.pop(pid,None)
        lease = None
    if data.get('action') == 'acquire' and (not lease or lease['owner'] == who):
        LEASES[pid] = lease = {'owner':who,'expires':time.monotonic()+30}
    if data.get('action') == 'heartbeat' and lease and lease['owner'] == who:
        lease['expires'] = time.monotonic()+30
    return web.json_response({'editing':bool(lease and lease['owner']==who),'occupied':bool(lease)})
