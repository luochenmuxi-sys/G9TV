import os
import numpy as np
import torch
from PIL import Image, ImageOps
import folder_paths
from .g9_config import PROJECTS

class StudioCanvasImage:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'image':('STRING',{'default':''}),'width':('INT',{'default':768,'min':32,'step':32}),'height':('INT',{'default':768,'min':32,'step':32}),'fit':(['follow','pad','crop'],)}}
    RETURN_TYPES=('IMAGE',)
    FUNCTION='load'
    CATEGORY='Simple Studio/Director'
    @classmethod
    def IS_CHANGED(cls,image,**kwargs):
        p=image
        return os.path.getmtime(p) if os.path.isfile(p) else float('nan')
    def load(self,image,width,height,fit):
        p=image
        root=os.path.realpath(PROJECTS)
        if os.path.commonpath([root,os.path.realpath(p)])!=root:raise ValueError('素材必须位于导演项目目录')
        with Image.open(p) as source:
            im=ImageOps.exif_transpose(source).convert('RGB')
            if fit=='crop':im=ImageOps.fit(im,(width,height),Image.Resampling.LANCZOS)
            elif fit=='pad':im=ImageOps.pad(im,(width,height),Image.Resampling.LANCZOS,color=(128,128,128))
            else:
                scale=(width*height/(im.width*im.height))**0.5
                im=im.resize((max(32,round(im.width*scale/32)*32),max(32,round(im.height*scale/32)*32)),Image.Resampling.LANCZOS)
            return (torch.from_numpy(np.array(im).astype(np.float32)/255).unsqueeze(0),)

class StudioCanvasVideo:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required':{'video':('STRING',{'default':''}),'seconds':('FLOAT',{'default':3,'min':1,'max':15}),'long_edge':('INT',{'default':736,'min':256,'max':1024,'step':32})}}
    RETURN_TYPES=('IMAGE',)
    FUNCTION='load'
    CATEGORY='Simple Studio/Director'
    def load(self,video,seconds,long_edge):
        import cv2,math
        root=os.path.realpath(PROJECTS)
        if os.path.commonpath([root,os.path.realpath(video)])!=root:raise ValueError('视频必须位于导演项目目录')
        cap=cv2.VideoCapture(video)
        try:
            fps=cap.get(cv2.CAP_PROP_FPS)
            if not cap.isOpened() or fps<=0:raise ValueError('无法读取参考视频')
            frames=[];count=0;next_target=0;limit=min(360,math.ceil(seconds*24))
            while len(frames)<limit:
                ok,frame=cap.read()
                if not ok:break
                target=int(count*24/fps);count+=1
                if target<next_target:continue
                h,w=frame.shape[:2];scale=min(1,long_edge/max(h,w));nw=max(32,round(w*scale/32)*32);nh=max(32,round(h*scale/32)*32)
                rgb=cv2.cvtColor(cv2.resize(frame,(nw,nh)),cv2.COLOR_BGR2RGB)
                while next_target<=target and len(frames)<limit:
                    frames.append(rgb);next_target+=1
            if not frames:raise ValueError('参考视频没有可读帧')
            if len(frames)<53:raise ValueError('H3参考视频需至少约2.2秒，请选择更长素材')
            aligned=max(5,((len(frames)-5)//17)*17+5)
            return (torch.from_numpy(np.stack(frames[:aligned]).astype(np.float32)/255),)
        finally:cap.release()

NODE_CLASS_MAPPINGS={'StudioCanvasImage':StudioCanvasImage,'StudioCanvasVideo':StudioCanvasVideo}
