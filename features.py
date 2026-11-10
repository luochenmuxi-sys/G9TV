"""Feature catalog and local installation API. Detection performs no downloads."""
import copy
import os
import platform
from pathlib import Path
import shutil
import time
from aiohttp import web
import folder_paths
import nodes
import torch
from server import PromptServer
from . import director as d
from .feature_engine import FeatureEngine
from .update_service import local_request


def hardware():
    result={'system':platform.system(),'machine':platform.machine(),'python':platform.python_version(),'torch':torch.__version__,'cuda':False,'vram_gb':0,'ram_gb':0,'gpu':'未检测到CUDA显卡','edge':False}
    try:
        import psutil
        result['ram_gb']=round(psutil.virtual_memory().total/1024**3,1)
    except ImportError:
        if os.name=='nt':
            import ctypes
            class Memory(ctypes.Structure):
                _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong),('total',ctypes.c_ulonglong),('available',ctypes.c_ulonglong),('page',ctypes.c_ulonglong),('page_available',ctypes.c_ulonglong),('virtual',ctypes.c_ulonglong),('virtual_available',ctypes.c_ulonglong),('reserved',ctypes.c_ulonglong)]
            memory=Memory();memory.length=ctypes.sizeof(memory);ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory));result['ram_gb']=round(memory.total/1024**3,1)
    try:
        if torch.cuda.is_available():
            device=torch.cuda.get_device_properties(0);result.update(cuda=True,gpu=device.name,vram_gb=round(device.total_memory/1024**3,1))
    except Exception as error:result['cuda_error']=str(error)
    result['edge']=any((Path(os.environ.get(key,default))/'Microsoft/Edge/Application/msedge.exe').is_file() for key,default in [('ProgramFiles(x86)','C:/Program Files (x86)'),('ProgramFiles','C:/Program Files')])
    return result


ENGINE=FeatureEngine(folder_paths,lambda:nodes.NODE_CLASS_MAPPINGS,d.ROOT/'_feature_installer',hardware)
stored_roots=ENGINE.storage/'model-roots.json'
if stored_roots.is_file():
    try:
        import json
        for model_root in json.loads(stored_roots.read_text(encoding='utf8')):
            try:ENGINE.add_model_root(model_root,persist=False)
            except (ValueError,OSError):pass
    except (ValueError,OSError):pass


@d.ROUTES.post('/simple-studio/features/model-root')
async def model_root(request):
    try:
        local_request(request);body=await request.json()
        if ENGINE.job and ENGINE.job['state']=='running':raise ValueError('安装进行中，暂不能修改模型路径')
        return web.json_response(ENGINE.add_model_root(body.get('path','')))
    except Exception as error:return d.response_error(error)


@d.ROUTES.get('/simple-studio/features')
async def status(request):
    try:
        local_request(request)
        import asyncio
        return web.json_response(await asyncio.to_thread(ENGINE.inspect))
    except Exception as error:return d.response_error(error)


@d.ROUTES.post('/simple-studio/features/plan')
async def plan(request):
    try:
        local_request(request);body=await request.json()
        return web.json_response(ENGINE.plan(body.get('features')))
    except Exception as error:return d.response_error(error)


@d.ROUTES.post('/simple-studio/features/install')
async def install(request):
    try:
        local_request(request);body=await request.json()
        if body.get('confirmed') is not True:raise ValueError('请先查看下载量和需求，再点击安装')
        running,pending=PromptServer.instance.prompt_queue.get_current_queue()
        if running or pending:raise ValueError('请等待生成队列清空后再安装组件')
        return web.json_response(ENGINE.start(body.get('features')))
    except Exception as error:return d.response_error(error)


@d.ROUTES.get('/simple-studio/features/job')
async def job(request):
    try:
        local_request(request)
        with ENGINE.lock:result=copy.deepcopy(ENGINE.job)
        return web.json_response(result or {'state':'idle'})
    except Exception as error:return d.response_error(error)


@d.ROUTES.post('/simple-studio/features/cancel')
async def cancel(request):
    try:
        local_request(request);ENGINE.stop.set()
        return web.json_response({'ok':True,'note':'当前下载文件完成后停止；校验可立即停止。'})
    except Exception as error:return d.response_error(error)
