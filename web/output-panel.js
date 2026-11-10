import { app } from "../../scripts/app.js";

app.registerExtension({
  name: "SimpleStudio.OutputPanel",
  async setup() {
    let panel;
    const roles={135:'video_duration',115:'video_dimensions',126:'video_schedule',131:'video_noise',405:'image_canvas',407:'image_sampler',5:'edit_export'};
    const find = (id, type) => app.graph?._nodes?.find(n => n.properties?.studio_role === roles[id] && (!type || n.type === type));
    const widget = (node, name) => node?.widgets?.find(w => w.name === name);
    function setValue(node, name, value) {
      const w = widget(node, name);
      if (!w) throw new Error(`当前节点没有参数 ${name}`);
      w.value = value;
      w.callback?.(value, app.canvas, node);
      node.setDirtyCanvas?.(true, true);
      app.graph.change?.();
    }
    function draw() {
      if (!panel) return;
      panel.replaceChildren();
      Object.assign(panel.style, {padding:"16px", overflowY:"auto", height:"100%", boxSizing:"border-box", color:"var(--fg-color)", background:"var(--comfy-menu-bg)", fontSize:"14px"});
      const title=document.createElement("h3"); title.textContent="基础输出参数"; panel.append(title);
      const help=document.createElement("p"); help.textContent="修改后直接同步到当前工作流。切换工作流后点击刷新。"; help.style.lineHeight="1.6"; panel.append(help);
      const refresh=document.createElement("button"); refresh.textContent="刷新当前工作流参数"; refresh.onclick=draw; panel.append(refresh);
      function control(label,node,name,opts={}) {
        const w=widget(node,name); if(!w)return;
        const row=document.createElement("label"); Object.assign(row.style,{display:"block",marginTop:"20px"});
        const caption=document.createElement("div"); caption.textContent=label; caption.style.marginBottom="8px"; row.append(caption);
        const choices=opts.choices || (Array.isArray(w.options?.values)?w.options.values:null);
        const field=document.createElement(choices?"select":"input");
        if(choices) for(const entry of choices){const option=document.createElement("option");option.value=typeof entry==="object"?entry.value:entry;option.textContent=typeof entry==="object"?entry.label:entry;field.append(option);}
        else {field.type=opts.text?"text":"number";field.step=opts.step||"1";if(opts.min!==undefined)field.min=opts.min;if(opts.max!==undefined)field.max=opts.max;}
        field.value=w.value;
        Object.assign(field.style,{width:"100%",padding:"10px",boxSizing:"border-box",background:"var(--comfy-input-bg)",color:"var(--input-text)",border:"1px solid var(--border-color)",borderRadius:"6px"});
        field.onchange=()=>{const value=choices||opts.text?field.value:Number(field.value);if(!choices&&!opts.text&&(!Number.isFinite(value)||(opts.min!==undefined&&value<opts.min)||(opts.max!==undefined&&value>opts.max))){field.value=w.value;return;}setValue(node,name,choices&&typeof w.value==="number"?Number(value):value);};
        row.append(field);panel.append(row);
      }
      const duration=find(135,"PrimitiveFloat"), resolution=find(115,"ResolutionSelector"), imageSize=find(405,"EmptySD3LatentImage");
      if(duration){
        control("视频时长（秒）",duration,"value",{min:1,max:30,step:0.5});
        control("画面比例",resolution,"aspect_ratio");
        control("分辨率（总像素）",resolution,"megapixels",{choices:[{value:0.3,label:"草稿 · 约0.3百万像素"},{value:0.6,label:"标准 · 约0.6百万像素"},{value:1,label:"精细 · 约1百万像素"},{value:1.5,label:"高分辨率 · 约1.5百万像素"}]});
        control("视频采样步数",find(126,"BasicScheduler"),"steps",{min:1,max:50});
        control("视频随机种子",find(131,"RandomNoise"),"noise_seed",{min:0});
        const note=document.createElement("p");note.textContent="H3 按24 fps生成，实际时长会按模型帧数规则取整。超过训练范围的长视频可能不稳定。分辨率随比例取整到32的倍数。";note.style.lineHeight="1.7";note.style.opacity="0.7";panel.append(note);
      } else if(imageSize){
        control("图片宽度",imageSize,"width",{min:256,max:2048,step:32});
        control("图片高度",imageSize,"height",{min:256,max:2048,step:32});
        control("图片采样步数",find(407,"KSampler"),"steps",{min:1,max:50});
        control("图片随机种子",find(407,"KSampler"),"seed",{min:0});
      } else if(find(5,"StudioEditExport")) {
        const edit=find(5,"StudioEditExport");
        control("导出宽度",edit,"width",{min:256,max:3840,step:2});
        control("导出高度",edit,"height",{min:256,max:3840,step:2});
        control("导出帧率",edit,"fps",{min:1,max:60});
        control("CRF画质（越低越清晰）",edit,"crf",{min:12,max:35});
        control("配乐音量",edit,"music_volume",{min:0,max:2,step:0.1});
      } else {const p=document.createElement("p");p.textContent="请打开 01_图片生成、02_视频生成 或 03_视频剪辑。";panel.append(p);}
    }
    app.extensionManager.registerSidebarTab({id:"simple-studio-output",icon:"pi pi-sliders-h",title:"基础输出",tooltip:"基础输出参数",type:"custom",render:(el)=>{panel=el;draw();}});
  }
});
