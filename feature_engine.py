"""Selective, restart-aware installation using a fixed component catalog."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from .feature_catalog import FEATURES, PLUGINS

PIP_LOCK = threading.Lock()
PROTECTED = ('torch', 'torchvision', 'torchaudio', 'numpy', 'transformers', 'tokenizers')


def sha256(path, stop=None):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024*1024),b''):
            if stop and stop.is_set():raise InterruptedError('安装已停止')
            result.update(chunk)
    return result.hexdigest()


def package_missing(spec):
    from packaging.requirements import Requirement
    requirement=Requirement(spec)
    try:return not requirement.specifier.contains(importlib.metadata.version(requirement.name),prereleases=True)
    except importlib.metadata.PackageNotFoundError:return True


def extract_node(content, destination):
    with zipfile.ZipFile(content) as archive:
        entries=archive.infolist()
        if sum(item.file_size for item in entries)>250*1024**2:raise ValueError('节点解压体积超限')
        for item in entries:
            path=PurePosixPath(item.filename)
            if len(path.parts)<2:continue
            relative=PurePosixPath(*path.parts[1:])
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename or ((item.external_attr>>16)&0o170000)==0o120000:
                raise ValueError('节点压缩包包含不安全路径')
            target=destination/relative
            if not target.resolve().is_relative_to(destination.resolve()):raise ValueError('节点路径越界')
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(item) as source,target.open('wb') as output:shutil.copyfileobj(source,output)
    if not (destination/'__init__.py').is_file():raise ValueError('下载的节点不完整')


class FeatureEngine:
    def __init__(self,folder_paths,node_classes,storage,hardware):
        self.paths=folder_paths;self.node_classes=node_classes;self.storage=Path(storage);self.hardware=hardware
        self.stop=threading.Event();self.job=None;self.lock=threading.Lock()

    def roots(self,category):
        key={'SEEDVR2':'seedvr2'}.get(category,category)
        try:values=list(self.paths.get_folder_paths(key))
        except (KeyError,ValueError):values=[]
        values.append(str(Path(self.paths.models_dir)/category))
        # Respect registered extra model paths, including older G9TV shared model stores.
        for name in ('checkpoints','diffusion_models','text_encoders','vae'):
            try:
                for base in self.paths.get_folder_paths(name):values.append(str(Path(base).parent/category))
            except (KeyError,ValueError):pass
        return list(dict.fromkeys(str(Path(value).resolve()) for value in values))

    def add_model_root(self,value,persist=True):
        path=Path(str(value)).expanduser()
        if not path.is_absolute() or not path.is_dir():raise ValueError('请输入已存在的模型根目录的绝对路径')
        path=path.resolve();categories={item['target'].split('/')[0] for entry in FEATURES.values() for item in entry['files']}
        found=[category for category in categories if (path/category).is_dir()]
        if not found:raise ValueError('该目录下没有已知模型分类目录，如 diffusion_models、checkpoints、vae')
        for category in found:self.paths.add_model_folder_path({'SEEDVR2':'seedvr2'}.get(category,category),str(path/category))
        if persist:
            self.storage.mkdir(parents=True,exist_ok=True);file=self.storage/'model-roots.json'
            values=json.loads(file.read_text(encoding='utf8')) if file.exists() else []
            values=list(dict.fromkeys([*values,str(path)]));temporary=file.with_suffix('.tmp');temporary.write_text(json.dumps(values),encoding='utf8');os.replace(temporary,file)
        return {'root':str(path),'categories':found}

    def file_status(self,item):
        category,relative=item['target'].split('/',1)
        candidates=[Path(root)/relative for root in self.roots(category)]
        for path in candidates:
            if path.is_file() and path.stat().st_size==item['size']:
                return {'target':item['target'],'size':item['size'],'present':True,'path':str(path),'integrity':'size_only'}
        existing=next((path for path in candidates if path.is_file()),None)
        return {'target':item['target'],'size':item['size'],'present':False,'path':str(existing or candidates[0]),'integrity':'size_mismatch' if existing else 'missing'}

    def plugin_dir(self,plugin):
        classes=self.node_classes()
        for name in plugin['classes']:
            cls=classes.get(name)
            if cls:
                module=sys.modules.get(cls.__module__)
                location=getattr(module,'__file__',None)
                if location:
                    for parent in Path(location).resolve().parents:
                        if parent.name in (plugin['folder'],plugin['repo'].split('/')[-1]):return parent
        for base in self.paths.get_folder_paths('custom_nodes'):
            path=Path(base)/plugin['folder']
            if path.is_dir():return path
        return None

    def inspect(self):
        machine=self.hardware();classes=self.node_classes();features=[]
        for key,entry in FEATURES.items():
            files=[self.file_status(item) for item in entry['files']]
            missing_core=[name for name in entry['required_nodes'] if name not in classes]
            plugins=[{'id':name,'directory':str(self.plugin_dir(PLUGINS[name]) or ''),'loaded':all(cls in classes for cls in PLUGINS[name]['classes']),'source':PLUGINS[name]['source']} for name in entry['plugins']]
            dependencies=[spec for spec in entry['packages'] if package_missing(spec)]
            missing_bytes=sum(item['size'] for item in files if not item['present'])
            warnings=[]
            if entry['minimum_vram_gb'] and not machine.get('cuda'):warnings.append('未检测到可用CUDA；此功能当前不建议使用')
            elif machine.get('vram_gb',0)+.15<entry['minimum_vram_gb']:warnings.append('显存低于本组合工程建议')
            if machine.get('ram_gb',0)<entry['minimum_ram_gb']:warnings.append('内存低于本组合工程建议')
            ready=not(missing_bytes or missing_core or dependencies or any(not p['loaded'] for p in plugins))
            features.append({'id':key,**entry,'files':files,'plugins':plugins,'missing_packages':dependencies,'missing_core_nodes':missing_core,'missing_model_bytes':missing_bytes,'total_model_bytes':sum(f['size'] for f in entry['files']),'status':'components_ready' if ready else 'incomplete','warnings':warnings,'generation_tested':False})
        return {'hardware':machine,'features':features,'note':'存在检测按文件大小进行，不代表完整性校验或生成验收。安装时会校验选中模型的SHA-256；硬件数值为工程建议。'}

    def plan(self,identifiers):
        if not isinstance(identifiers,list) or not identifiers or len(identifiers)>len(FEATURES) or any(key not in FEATURES for key in identifiers):raise ValueError('请选择已列出的功能')
        files={item['target']:item for key in identifiers for item in FEATURES[key]['files']}
        missing=[item for item in files.values() if not self.file_status(item)['present']]
        by_volume={}
        for item in missing:
            category=item['target'].split('/')[0];target=Path(self.roots(category)[0]);parent=target
            while not parent.exists():parent=parent.parent
            drive=str(parent.anchor);by_volume.setdefault(drive,{'required':0,'free':shutil.disk_usage(parent).free})['required']+=item['size']
        for volume in by_volume.values():
            volume['required']=int(volume['required']*1.15)+2*1024**3
            volume['enough']=volume['free']>=volume['required']
        variants=[item['target'] for item in files.values() if self.file_status(item)['integrity']=='size_mismatch']
        return {'features':list(dict.fromkeys(identifiers)),'download_model_bytes':sum(item['size'] for item in missing),'reused_model_bytes':sum(item['size'] for item in files.values() if self.file_status(item)['present']),'variant_files':variants,'volumes':by_volume,'note':'下载量仅含模型；节点和Python依赖另计，已有模型会先校验再复用。完整校验失败时实际下载量可能增加。'}

    def update(self,**values):
        with self.lock:self.job.update(values)

    def pip(self,arguments):
        self.storage.mkdir(parents=True,exist_ok=True)
        constraints=self.storage/'protected-packages.txt';pins=[]
        for name in PROTECTED:
            try:pins.append(name+'=='+importlib.metadata.version(name))
            except importlib.metadata.PackageNotFoundError:pass
        constraints.write_text('\n'.join(pins),encoding='utf8')
        command=[sys.executable,'-m','pip','install','--disable-pip-version-check','--upgrade-strategy','only-if-needed','-c',str(constraints),*arguments]
        with PIP_LOCK:
            result=subprocess.run(command,capture_output=True,text=True,timeout=1800,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise ValueError('依赖安装失败；未自动升级PyTorch等受保护组件。'+(result.stderr or result.stdout)[-1200:])

    def install_plugin(self,key):
        plugin=PLUGINS[key];directory=self.plugin_dir(plugin)
        if directory:
            self.update(stage='复用本地节点：'+key)
        else:
            self.update(stage='下载节点：'+key)
            req=urllib.request.Request(plugin['url'],headers={'User-Agent':'G9TV-feature-installer'})
            with urllib.request.urlopen(req,timeout=60) as response:content=response.read(60*1024**2+1)
            if len(content)>60*1024**2 or hashlib.sha256(content).hexdigest()!=plugin['sha256']:raise ValueError('节点下载校验失败：'+key)
            import io
            base=Path(self.paths.get_folder_paths('custom_nodes')[0]);base.mkdir(parents=True,exist_ok=True)
            directory=base/key
            with tempfile.TemporaryDirectory(prefix='.g9-node-',dir=base) as temporary:
                stage=Path(temporary)/key;stage.mkdir();extract_node(io.BytesIO(content),stage)
                if directory.exists():raise ValueError('节点目录已存在，未覆盖：'+key)
                stage.rename(directory)
        requirements=directory/'requirements.txt'
        if requirements.is_file():
            self.update(stage='安装节点依赖：'+key);self.pip(['-r',str(requirements)])

    def install_model(self,item):
        status=self.file_status(item);target=Path(status['path'])
        if status['present']:
            self.update(stage='校验已有模型：'+item['target'])
            if sha256(target,self.stop)==item['sha256']:return
        category,relative=item['target'].split('/',1);target=Path(self.roots(category)[0])/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        if shutil.disk_usage(target.parent).free<item['size']+512*1024**2:raise ValueError('模型目录磁盘空间不足')
        self.update(stage='下载模型：'+item['target'])
        from huggingface_hub import hf_hub_download
        from urllib.parse import urlsplit,unquote
        parts=urlsplit(item['url']).path.strip('/').split('/')
        if parts[2]!='resolve':raise ValueError('模型下载清单不正确')
        cache=target.parent/'.g9-downloads'/item['sha256']
        try:downloaded=Path(hf_hub_download(repo_id='/'.join(parts[:2]),revision=parts[3],filename=unquote('/'.join(parts[4:])),local_dir=str(cache),endpoint='https://huggingface.co'))
        except Exception as error:
            if item.get('gated'):raise ValueError('此模型需要先登录 Hugging Face 接受许可，并在本机配置 HF 登录；也可手动下载后重新检测。') from error
            raise
        if self.stop.is_set():raise InterruptedError('已停止；已完成的下载可以复用')
        self.update(stage='校验下载：'+item['target'])
        if downloaded.stat().st_size!=item['size'] or sha256(downloaded,self.stop)!=item['sha256']:raise ValueError('模型完整性校验失败：'+item['target'])
        if target.exists():target.rename(target.with_name(target.name+'.g9-backup-'+str(time.time_ns())))
        os.replace(downloaded,target)

    def start(self,identifiers):
        plan=self.plan(identifiers)
        if any(not volume['enough'] for volume in plan['volumes'].values()):raise ValueError('磁盘空间不足，尚未开始下载')
        with self.lock:
            if self.job and self.job['state']=='running':raise ValueError('已有功能安装任务正在运行')
            self.stop.clear();self.job={'state':'running','features':plan['features'],'stage':'准备安装','completed':0,'total':0,'errors':[],'restartRequired':False,'plan':plan}
        threading.Thread(target=self.run,args=(plan['features'],),daemon=True,name='G9TV-feature-install').start()
        return plan

    def run(self,identifiers):
        try:
            packages=list(dict.fromkeys(spec for key in identifiers for spec in FEATURES[key]['packages'] if package_missing(spec)))
            if packages:self.update(stage='安装缺失的Python依赖');self.pip(packages)
            plugins=list(dict.fromkeys(name for key in identifiers for name in FEATURES[key]['plugins']))
            for key in plugins:
                if self.stop.is_set():raise InterruptedError('安装已停止')
                if not all(name in self.node_classes() for name in PLUGINS[key]['classes']):self.install_plugin(key)
            files={item['target']:item for key in identifiers for item in FEATURES[key]['files']}
            self.update(total=len(files))
            for number,item in enumerate(files.values(),1):
                if self.stop.is_set():raise InterruptedError('安装已停止')
                self.install_model(item);self.update(completed=number)
            missing=sorted({name for key in identifiers for name in FEATURES[key]['required_nodes'] if name not in self.node_classes()})
            self.update(state='needs_restart' if plugins or packages else 'finished',stage='组件处理完成，请重启ComfyUI并重新检测。' if plugins or packages else '所选组件检查完成，请重新检测。',restartRequired=bool(plugins or packages),missingCoreNodes=missing)
            if missing:self.update(stage='组件处理完成；仍缺少核心节点，请更新ComfyUI后重新检测：'+', '.join(missing))
        except InterruptedError as error:self.update(state='cancelled',stage=str(error))
        except Exception as error:
            import re
            message=re.sub(r'hf_[A-Za-z0-9]{12,}','<REDACTED>',str(error))
            self.update(state='failed',stage='安装未完成，可重新检测后重试',errors=[message])
