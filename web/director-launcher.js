import { app } from '../../scripts/app.js';
app.registerExtension({
 name:'SimpleStudio.Director',
 async setup(){
  let overlay;
  function open(){
   if(overlay){overlay.style.display='block';return;}
   overlay=document.createElement('div');overlay.id='simple-studio-director';
   Object.assign(overlay.style,{position:'fixed',inset:'0',zIndex:'10000',background:'#0f131a'});
   const iframe=document.createElement('iframe');iframe.src='/simple-studio/ui';iframe.title='G9 TV';
   Object.assign(iframe.style,{width:'100%',height:'100%',border:'0'});overlay.append(iframe);document.body.append(overlay);
  }
  window.addEventListener('message',async e=>{
   if(e.origin!==location.origin||!overlay||e.source!==overlay.querySelector('iframe').contentWindow)return;
   if(e.data?.type==='studio:close')overlay.style.display='none';
   if(e.data?.type==='studio:workflow'){
    if(app.graph?._nodes?.length&&!confirm('打开调试工作流会替换当前节点画布。请先保存现有节点工作流。继续？'))return;
    await app.loadGraphData(e.data.workflow);overlay.style.display='none';
   }
  });
  app.extensionManager.registerSidebarTab({id:'studio-director',icon:'pi pi-video',title:'G9 TV',tooltip:'G9 TV 本地创作工作台',type:'custom',render(el){
   el.replaceChildren();const wrap=document.createElement('div');wrap.style.padding='20px';
   const h=document.createElement('h2');h.textContent='G9 TV';const p=document.createElement('p');p.textContent='素材连线 · 参考生图 · H3视频';
   const b=document.createElement('button');b.textContent='打开 G9 TV ↗';b.onclick=open;b.style.padding='12px';wrap.append(h,p,b);el.append(wrap);
  }});
  window.simpleStudioOpen=open;
 }
});
