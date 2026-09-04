#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, urllib.parse, yaml
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from renderer import DEFAULT_SPEC, normalize_spec, render_outputs
from auto_tune import playfulness_score, tune as autotune_spec

HERE = Path(__file__).resolve().parent
VENDOR = HERE / 'vendor' / 'qrcode-js'
MODULE_FILES = [
    'QRMode.js','QRErrorCorrectLevel.js','QRMaskPattern.js','QRMath.js',
    'QRPolynomial.js','QRRSBlock.js','QRBitBuffer.js','QR8bitByte.js','QRUtil.js','index.js'
]

def load_config(path: Path | None):
    if not path:
        return normalize_spec(DEFAULT_SPEC)
    txt = path.read_text(encoding='utf-8')
    data = json.loads(txt) if path.suffix.lower() == '.json' else (yaml.safe_load(txt) or {})
    return normalize_spec(data)

def browser_bundle() -> str:
    chunks = ["const __mods={}; const __cache={}; function __req(name){ if(__cache[name]) return __cache[name].exports; const m={exports:{}}; __cache[name]=m; __mods[name](__req,m,m.exports); return m.exports; }"]
    mapping = {
        'QRMode.js':'./QRMode','QRErrorCorrectLevel.js':'./QRErrorCorrectLevel','QRMaskPattern.js':'./QRMaskPattern',
        'QRMath.js':'./QRMath','QRPolynomial.js':'./QRPolynomial','QRRSBlock.js':'./QRRSBlock',
        'QRBitBuffer.js':'./QRBitBuffer','QR8bitByte.js':'./QR8bitByte','QRUtil.js':'./QRUtil','index.js':'./index'
    }
    for fn in MODULE_FILES:
        src=(VENDOR/fn).read_text(encoding='utf-8')
        name=mapping[fn]
        chunks.append(f"__mods[{json.dumps(name)}]=function(require,module,exports){{\n{src}\n}};")
    chunks.append("window.QRCodeOffline=__req('./index'); window.QRErrorCorrectLevelOffline=__req('./QRErrorCorrectLevel');")
    return '\n'.join(chunks)

def static_html(spec: dict) -> str:
    bundle = browser_bundle().replace('</script>', '<\\/script>')
    initial = json.dumps(spec, ensure_ascii=False).replace('</script>', '<\\/script>')
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>QR Designer</title>
<style>
:root{{font-family:Inter,system-ui,sans-serif;color:#202124;background:#f6f5f2}}*{{box-sizing:border-box}}body{{margin:0}}.app{{display:grid;grid-template-columns:minmax(360px,460px) 1fr;min-height:100vh}}.controls{{padding:22px;background:#fff;border-right:1px solid #ddd;overflow:auto}}.preview{{display:flex;align-items:center;justify-content:center;padding:28px;position:sticky;top:0;height:100vh}}.card{{background:#fff;border:1px solid #ddd;border-radius:18px;padding:22px;box-shadow:0 8px 30px #0001;max-width:700px;width:100%}}#qr{{display:flex;justify-content:center;align-items:center;min-height:480px}}#qr svg{{width:min(70vh,580px);height:auto;max-width:100%}}h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:14px;text-transform:uppercase;letter-spacing:.06em;margin:24px 0 10px;color:#686868}}.sub,.tiny{{color:#777;font-size:12px}}.sub{{margin-bottom:20px}}label{{display:block;font-size:12px;font-weight:700;margin:11px 0 5px}}.row{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.row3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}}input,select,button,textarea{{font:inherit}}input[type=text],input[type=url],input[type=number],select{{width:100%;padding:9px 10px;border:1px solid #ccc;border-radius:9px;background:white}}input[type=color]{{width:100%;height:39px;border:1px solid #ccc;border-radius:9px;background:white;padding:3px}}.range{{display:grid;grid-template-columns:1fr 58px;gap:8px}}.buttons{{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}}button{{border:0;border-radius:9px;padding:9px 12px;background:#222;color:#fff;cursor:pointer}}.secondary{{background:#ecebea;color:#222}}.status{{font-size:13px;margin-top:15px;padding:10px;border-radius:9px;background:#eee}}.runtime{{font-size:12px;margin:8px 0;padding:8px 10px;border-radius:9px;background:#fee;color:#811}}.ok{{background:#e7f6ec;color:#176b35}}.warn{{background:#fff5d8;color:#765600}}.check{{display:flex;align-items:center;gap:7px;margin-top:10px}}.check label{{margin:0;font-weight:500}}.modal{{position:fixed;inset:0;background:#0008;display:none;align-items:center;justify-content:center;padding:20px;z-index:20}}.modal.open{{display:flex}}.modal-card{{width:min(760px,100%);background:#fff;border-radius:16px;padding:18px;box-shadow:0 20px 60px #0005}}.modal-head{{display:flex;align-items:center;justify-content:space-between;gap:10px}}#yamlText{{width:100%;height:420px;margin:12px 0;padding:12px;border:1px solid #ccc;border-radius:10px;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace}}.small-note{{font-size:11px;color:#777;margin-top:8px}}details{{border:1px solid #ececec;border-radius:12px;padding:10px 12px;margin-top:10px}}summary{{cursor:pointer;font-weight:700;font-size:12px;color:#444}}@media(max-width:850px){{.app{{grid-template-columns:1fr}}.preview{{height:auto;position:relative;order:-1}}.controls{{border-right:0}}#qr{{min-height:320px}}}}
</style></head><body><div class="app"><section class="controls"><h1>QR Designer</h1><div class="sub">Python-backed QR schema with offline preview, YAML export, gradients, mixed module shapes, experimental star / petal / soft-blob modules, and logo badge options.</div>
<div id="runtime" class="runtime">JavaScript has not started. If this message stays red, open the downloaded HTML in Safari/Chrome instead of the in-app file preview.</div>
<noscript><div class="runtime">JavaScript is disabled in this viewer. Download this HTML and open it in a normal browser.</div></noscript>
<label>URL / text</label><input id="value" type="url"><div class="buttons"><button id="generateBtn" type="button">Generate / Refresh QR</button><button class="secondary" id="autotuneBtn" type="button">Auto-tune (localhost)</button></div>
<div class="row"><div><label>Error correction</label><select id="ec"><option>L</option><option>M</option><option>Q</option><option>H</option></select></div><div><label>Quiet zone</label><input id="quiet" type="number" min="0" max="10"></div></div>
<h2>Modules</h2>
<div class="row3"><div><label>Primary shape</label><select id="moduleShape"><option>square</option><option>rounded</option><option>circle</option><option>diamond</option><option>squircle</option><option>star</option><option>petal</option><option>soft_blob</option></select></div><div><label>Accent shape</label><select id="moduleShape2"><option value="">(same)</option><option>square</option><option>rounded</option><option>circle</option><option>diamond</option><option>squircle</option><option>star</option><option>petal</option><option>soft_blob</option></select></div><div><label>Pattern</label><select id="modulePattern"><option>uniform</option><option>checker</option><option>diagonal</option><option>rings</option></select></div></div>
<div class="row"><div><label>Primary color</label><input id="moduleColor" type="color"></div><div><label>Accent color</label><input id="moduleColor2" type="color"></div></div>
<label>Module scale</label><div class="range"><input id="moduleScale" type="range" min=".35" max="1" step=".01"><span id="moduleScaleOut"></span></div>
<label>Roundness</label><div class="range"><input id="moduleRadius" type="range" min="0" max=".5" step=".01"><span id="moduleRadiusOut"></span></div>
<details><summary>Gradient</summary>
<div class="check"><input id="gradEnabled" type="checkbox"><label for="gradEnabled">Enable module gradient</label></div>
<div class="row3"><div><label>Type</label><select id="gradType"><option>linear</option><option>radial</option></select></div><div><label>From</label><input id="gradFrom" type="color"></div><div><label>To</label><input id="gradTo" type="color"></div></div>
<label>Linear angle</label><div class="range"><input id="gradAngle" type="range" min="0" max="360" step="1"><span id="gradAngleOut"></span></div>
<div class="small-note">When gradient is enabled it overrides solid module colors. Pattern still affects shape mixing.</div>
</details>
<h2>Finder eyes</h2>
<div class="row"><div><label>Frame</label><select id="eyeFrame"><option>square</option><option>rounded</option><option>circle</option><option>diamond</option><option>squircle</option></select></div><div><label>Pupil</label><select id="eyePupil"><option>square</option><option>rounded</option><option>circle</option><option>diamond</option><option>squircle</option></select></div></div>
<div class="row"><div><label>Eye color</label><input id="eyeColor" type="color"></div><div><label>Pupil color</label><input id="pupilColor" type="color"></div></div>
<label>Eye roundness</label><div class="range"><input id="eyeRadius" type="range" min="0" max=".5" step=".01"><span id="eyeRadiusOut"></span></div>
<h2>Background</h2><label>Color</label><input id="bg" type="color">
<h2>Logo</h2>
<div class="check"><input id="logoEnabled" type="checkbox"><label for="logoEnabled">Enable center logo path in exported YAML</label></div>
<input id="logoPath" type="text" placeholder="assets/instagram.svg">
<label>Logo scale</label><div class="range"><input id="logoScale" type="range" min=".05" max=".30" step=".01"><span id="logoScaleOut"></span></div>
<div class="check"><input id="knockout" type="checkbox"><label for="knockout">Background knockout behind logo</label></div>
<details><summary>Logo badge</summary>
<div class="check"><input id="badgeEnabled" type="checkbox"><label for="badgeEnabled">Enable logo badge</label></div>
<div class="row"><div><label>Badge shape</label><select id="badgeShape"><option>circle</option><option>rounded</option><option>square</option><option>squircle</option></select></div><div><label>Badge fill</label><input id="badgeColor" type="color"></div></div>
<div class="row"><div><label>Badge stroke</label><input id="badgeStrokeColor" type="color"></div><div><label>Stroke width (modules)</label><input id="badgeStrokeWidth" type="number" min="0" max="1" step="0.05"></div></div>
<div class="row"><div><label>Badge padding (modules)</label><input id="badgePadding" type="number" min="0" max="3" step="0.05"></div><div><label>Badge radius (modules)</label><input id="badgeRadius" type="number" min="0" max="3" step="0.05"></div></div>
</details>
<h2>Export</h2><div class="buttons"><button id="svgBtn">Download preview SVG</button><button id="pngBtn">Download preview PNG</button><button class="secondary" id="showYamlBtn">Show YAML</button><button class="secondary" id="yamlBtn">Download YAML</button><button class="secondary" id="jsonBtn">Download JSON</button></div><div id="status" class="status warn">Waiting for JavaScript…</div><p class="tiny">Standalone preview does not embed local logo files. Export YAML and run the Python generator for the final validated QR.</p></section><section class="preview"><div class="card"><div id="qr"></div></div></section></div>
<div id="yamlModal" class="modal"><div class="modal-card"><div class="modal-head"><h1>Current YAML</h1><button class="secondary" id="closeYaml">Close</button></div><textarea id="yamlText" spellcheck="false"></textarea><div class="buttons"><button id="copyYaml">Copy YAML</button><button class="secondary" id="downloadYamlModal">Download YAML</button></div></div></div>
<script>{bundle}</script>
<script>
const INITIAL_SPEC={initial}; const $=x=>document.getElementById(x); let lastSvg='',timer;
const EC={{L:1,M:0,Q:3,H:2}};
function clamp(v,a,b){{return Math.max(a,Math.min(b,v));}}
function hexToRgb(h){{h=String(h||'#000').replace('#',''); if(h.length===3) h=h.split('').map(x=>x+x).join(''); return [parseInt(h.slice(0,2),16),parseInt(h.slice(2,4),16),parseInt(h.slice(4,6),16)];}}
function rgbToHex(r,g,b){{return '#'+[r,g,b].map(v=>clamp(Math.round(v),0,255).toString(16).padStart(2,'0')).join('');}}
function lerpColor(a,b,t){{const A=hexToRgb(a),B=hexToRgb(b); t=clamp(+t,0,1); return rgbToHex(A[0]+(B[0]-A[0])*t,A[1]+(B[1]-A[1])*t,A[2]+(B[2]-A[2])*t);}}
function squirclePath(x,y,size){{const k=0.16*size, x2=x+size, y2=y+size, mx=x+size/2, my=y+size/2; return `M ${{mx}},${{y}} C ${{x2-k}},${{y}} ${{x2}},${{y+k}} ${{x2}},${{my}} C ${{x2}},${{y2-k}} ${{x2-k}},${{y2}} ${{mx}},${{y2}} C ${{x+k}},${{y2}} ${{x}},${{y2-k}} ${{x}},${{my}} C ${{x}},${{y+k}} ${{x+k}},${{y}} ${{mx}},${{y}} Z`;}}
function shape(sh,x,y,size,color,radius){{if(sh==='circle'){{let r=size/2;return `<circle cx="${{x+r}}" cy="${{y+r}}" r="${{r}}" fill="${{color}}"/>`}} if(sh==='diamond'){{let h=size/2;return `<polygon points="${{x+h}},${{y}} ${{x+size}},${{y+h}} ${{x+h}},${{y+size}} ${{x}},${{y+h}}" fill="${{color}}"/>`}} if(sh==='rounded'){{let r=size*radius;return `<rect x="${{x}}" y="${{y}}" width="${{size}}" height="${{size}}" rx="${{r}}" ry="${{r}}" fill="${{color}}"/>`}} if(sh==='squircle') return `<path d="${{squirclePath(x,y,size)}}" fill="${{color}}"/>`; return `<rect x="${{x}}" y="${{y}}" width="${{size}}" height="${{size}}" fill="${{color}}"/>`;}}
function eye(sh,x,y,m,color,bg,ps,pc,rad){{return shape(sh,x,y,7*m,color,rad)+shape(sh,x+m,y+m,5*m,bg,rad)+shape(ps,x+2*m,y+2*m,3*m,pc,rad)}}
function finderCell(x,y,n){{return (x<7&&y<7)||(x>=n-7&&y<7)||(x<7&&y>=n-7)}}
function accent(pattern,x,y,n){{if(pattern==='checker') return (x+y)%2===0; if(pattern==='diagonal') return ((x-y)%3+3)%3===0; if(pattern==='rings'){{const c=(n-1)/2, d=Math.hypot(x-c,y-c); return Math.floor(d/2.25)%2===0;}} return false;}}
function gradientColor(grad,cx,cy,px){{const a=grad.from||'#111111', b=grad.to||a; if(!grad.enabled) return null; if(grad.type==='radial'){{const c=px/2, maxd=Math.hypot(c,c)||1; return lerpColor(a,b,Math.hypot(cx-c,cy-c)/maxd);}} const ang=(+grad.angle||45)*Math.PI/180, ux=Math.cos(ang), uy=Math.sin(ang); const proj=cx*ux+cy*uy, maxproj=px*(Math.abs(ux)+Math.abs(uy))||1; return lerpColor(a,b,proj/maxproj);}}
function spec(){{return{{value:$('value').value,error_correction:$('ec').value,quiet_zone:+$('quiet').value,module_size:12,modules:{{shape:$('moduleShape').value,secondary_shape:$('moduleShape2').value||null,pattern:$('modulePattern').value,scale:+$('moduleScale').value,color:$('moduleColor').value,secondary_color:$('moduleColor2').value||null,radius:+$('moduleRadius').value,gradient:{{enabled:$('gradEnabled').checked,type:$('gradType').value,from:$('gradFrom').value,to:$('gradTo').value,angle:+$('gradAngle').value}}}},eyes:{{frame_shape:$('eyeFrame').value,pupil_shape:$('eyePupil').value,color:$('eyeColor').value,pupil_color:$('pupilColor').value,radius:+$('eyeRadius').value}},background:{{color:$('bg').value}},logo:{{enabled:$('logoEnabled').checked,path:$('logoPath').value||null,scale:+$('logoScale').value,knockout:$('knockout').checked,padding_modules:.65,radius_modules:.8,badge_enabled:$('badgeEnabled').checked,badge_shape:$('badgeShape').value,badge_color:$('badgeColor').value,badge_stroke_color:$('badgeStrokeColor').value,badge_stroke_width_modules:+$('badgeStrokeWidth').value,badge_padding_modules:+$('badgePadding').value,badge_radius_modules:+$('badgeRadius').value}},output:{{validate:true}}}}}}
function outs(){{$('moduleScaleOut').textContent=(+$('moduleScale').value).toFixed(2);$('moduleRadiusOut').textContent=(+$('moduleRadius').value).toFixed(2);$('eyeRadiusOut').textContent=(+$('eyeRadius').value).toFixed(2);$('logoScaleOut').textContent=(+$('logoScale').value).toFixed(2);$('gradAngleOut').textContent=Math.round(+$('gradAngle').value)+'°';}}
function set(s){{$('value').value=s.value;$('ec').value=s.error_correction;$('quiet').value=s.quiet_zone;$('moduleShape').value=s.modules.shape;$('moduleShape2').value=s.modules.secondary_shape||'';$('modulePattern').value=s.modules.pattern||'uniform';$('moduleScale').value=s.modules.scale;$('moduleColor').value=s.modules.color;$('moduleColor2').value=s.modules.secondary_color||s.modules.color;$('moduleRadius').value=s.modules.radius;$('gradEnabled').checked=!!(s.modules.gradient&&s.modules.gradient.enabled);$('gradType').value=(s.modules.gradient&&s.modules.gradient.type)||'linear';$('gradFrom').value=(s.modules.gradient&&s.modules.gradient.from)||s.modules.color;$('gradTo').value=(s.modules.gradient&&s.modules.gradient.to)||s.modules.color;$('gradAngle').value=(s.modules.gradient&&s.modules.gradient.angle)||45;$('eyeFrame').value=s.eyes.frame_shape;$('eyePupil').value=s.eyes.pupil_shape;$('eyeColor').value=s.eyes.color;$('pupilColor').value=s.eyes.pupil_color||s.eyes.color;$('eyeRadius').value=s.eyes.radius;$('bg').value=s.background.color;$('logoEnabled').checked=s.logo.enabled;$('logoPath').value=s.logo.path||'';$('logoScale').value=s.logo.scale;$('knockout').checked=s.logo.knockout;$('badgeEnabled').checked=!!s.logo.badge_enabled;$('badgeShape').value=s.logo.badge_shape||'circle';$('badgeColor').value=s.logo.badge_color||'#ffffff';$('badgeStrokeColor').value=s.logo.badge_stroke_color||'#000000';$('badgeStrokeWidth').value=s.logo.badge_stroke_width_modules||0;$('badgePadding').value=s.logo.badge_padding_modules||0.9;$('badgeRadius').value=s.logo.badge_radius_modules||1.0;outs()}}
function render(){{outs(); try{{$('runtime').textContent='JavaScript active — live preview enabled.';$('runtime').className='runtime ok'; const s=spec(); if(!s.value.trim()){{$('qr').innerHTML='<div class="tiny">Enter a URL or text, then press Generate / Refresh QR.</div>';$('status').textContent='Waiting for URL / text.';$('status').className='status warn';lastSvg='';return;}} const qr=new QRCodeOffline(-1,EC[s.error_correction]); qr.addData(s.value); qr.make(); const n=qr.getModuleCount(), q=s.quiet_zone, m=12, px=(n+2*q)*m, bg=s.background.color, mc=s.modules.color, ec=s.eyes.color||mc, pc=s.eyes.pupil_color||ec, ds=m*s.modules.scale, ins=(m-ds)/2; let a=[`<svg xmlns="http://www.w3.org/2000/svg" width="${{px}}" height="${{px}}" viewBox="0 0 ${{px}} ${{px}}"><rect width="100%" height="100%" fill="${{bg}}"/><g>`]; for(let y=0;y<n;y++) for(let x=0;x<n;x++) if(qr.isDark(y,x)&&!finderCell(x,y,n)){{ const on=accent(s.modules.pattern||'uniform',x,y,n); const sh=(on&&(s.modules.secondary_shape||''))?s.modules.secondary_shape:s.modules.shape; let color=(on&&(s.modules.secondary_color||''))?s.modules.secondary_color:mc; const gcol=gradientColor(s.modules.gradient||{{}}, (x+q)*m+m/2, (y+q)*m+m/2, px); if(gcol) color=gcol; a.push(shape(sh,(x+q)*m+ins,(y+q)*m+ins,ds,color,s.modules.radius)); }} a.push('</g><g>'); [[0,0],[n-7,0],[0,n-7]].forEach(([x,y])=>a.push(eye(s.eyes.frame_shape,(x+q)*m,(y+q)*m,m,ec,bg,s.eyes.pupil_shape,pc,s.eyes.radius))); if(s.logo.enabled){{ const ls=n*m*s.logo.scale, lx=(px-ls)/2, ly=(px-ls)/2; const pad=(s.logo.badge_enabled?+s.logo.badge_padding_modules:0.65)*m; const bs=ls+2*pad, bx=lx-pad, by=ly-pad; if(s.logo.knockout) a.push(`<rect x="${{bx}}" y="${{by}}" width="${{bs}}" height="${{bs}}" rx="${{0.8*m}}" fill="${{bg}}"/>`); if(s.logo.badge_enabled){{ let node=shape(s.logo.badge_shape,bx,by,bs,s.logo.badge_color||'#fff', clamp((+s.logo.badge_radius_modules||1)/(bs/m),0,.5)); const sw=(+s.logo.badge_stroke_width_modules||0)*m; if(sw>0) node=node.replace('/>',` stroke="${{s.logo.badge_stroke_color||'#000'}}" stroke-width="${{sw}}"/>`); a.push(node); }} a.push(`<text x="${{px/2}}" y="${{px/2+4}}" text-anchor="middle" font-family="system-ui,sans-serif" font-size="12" fill="#666">logo preview</text>`); }} a.push('</g></svg>'); lastSvg=a.join(''); $('qr').innerHTML=lastSvg; $('status').textContent=(s.logo.enabled?'Logo preview placeholder shown; exported YAML keeps the real logo path. ':'')+'Export YAML and run Python for the final scan-validated output.'; $('status').className='status warn'; }} catch(e){{$('status').textContent='Error: '+e.message;$('status').className='status warn';}}}}
function queue(){{clearTimeout(timer);timer=setTimeout(render,100)}}
function yq(v){{if(v===null||v===undefined)return 'null'; if(typeof v==='boolean')return v?'true':'false'; if(typeof v==='number')return Number.isInteger(v)?String(v):String(v); return JSON.stringify(String(v));}}
function yamlDump(s){{return `value: ${{yq(s.value)}}\nerror_correction: ${{s.error_correction}}\nquiet_zone: ${{s.quiet_zone}}\nmodule_size: ${{s.module_size}}\nmodules:\n  shape: ${{s.modules.shape}}\n  secondary_shape: ${{yq(s.modules.secondary_shape)}}\n  pattern: ${{s.modules.pattern}}\n  scale: ${{s.modules.scale}}\n  color: ${{yq(s.modules.color)}}\n  secondary_color: ${{yq(s.modules.secondary_color)}}\n  radius: ${{s.modules.radius}}\n  gradient:\n    enabled: ${{s.modules.gradient.enabled}}\n    type: ${{s.modules.gradient.type}}\n    from: ${{yq(s.modules.gradient.from)}}\n    to: ${{yq(s.modules.gradient.to)}}\n    angle: ${{s.modules.gradient.angle}}\neyes:\n  frame_shape: ${{s.eyes.frame_shape}}\n  pupil_shape: ${{s.eyes.pupil_shape}}\n  color: ${{yq(s.eyes.color)}}\n  pupil_color: ${{yq(s.eyes.pupil_color)}}\n  radius: ${{s.eyes.radius}}\nbackground:\n  color: ${{yq(s.background.color)}}\nlogo:\n  enabled: ${{s.logo.enabled}}\n  path: ${{yq(s.logo.path)}}\n  scale: ${{s.logo.scale}}\n  knockout: ${{s.logo.knockout}}\n  padding_modules: ${{s.logo.padding_modules}}\n  radius_modules: ${{s.logo.radius_modules}}\n  badge_enabled: ${{s.logo.badge_enabled}}\n  badge_shape: ${{s.logo.badge_shape}}\n  badge_color: ${{yq(s.logo.badge_color)}}\n  badge_stroke_color: ${{yq(s.logo.badge_stroke_color)}}\n  badge_stroke_width_modules: ${{s.logo.badge_stroke_width_modules}}\n  badge_padding_modules: ${{s.logo.badge_padding_modules}}\n  badge_radius_modules: ${{s.logo.badge_radius_modules}}\noutput:\n  validate: true\n`;}}
function dl(n,b){{let a=document.createElement('a'); a.href=URL.createObjectURL(b); a.download=n; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);}}
async function pngBlob(){{return new Promise((resolve,reject)=>{{const blob=new Blob([lastSvg],{{type:'image/svg+xml'}}),url=URL.createObjectURL(blob),im=new Image(); im.onload=()=>{{const c=document.createElement('canvas'); c.width=im.width*4; c.height=im.height*4; const g=c.getContext('2d'); g.scale(4,4); g.drawImage(im,0,0); URL.revokeObjectURL(url); c.toBlob(resolve,'image/png')}}; im.onerror=reject; im.src=url;}})}}
document.querySelectorAll('input,select').forEach(e=>{{e.addEventListener('input',queue); e.addEventListener('change',queue);}}); $('value').addEventListener('keyup',queue); $('generateBtn').onclick=render; $('autotuneBtn').onclick=autoTune; $('svgBtn').onclick=()=>dl('qr-preview.svg',new Blob([lastSvg],{{type:'image/svg+xml'}})); $('pngBtn').onclick=async()=>dl('qr-preview.png',await pngBlob()); $('jsonBtn').onclick=()=>dl('qr.json',new Blob([JSON.stringify(spec(),null,2)],{{type:'application/json'}})); $('yamlBtn').onclick=()=>dl('qr.yaml',new Blob([yamlDump(spec())],{{type:'text/yaml'}})); function openYaml(){{$('yamlText').value=yamlDump(spec()); $('yamlModal').classList.add('open');}} $('showYamlBtn').onclick=openYaml; $('closeYaml').onclick=()=>$('yamlModal').classList.remove('open'); $('copyYaml').onclick=async()=>{{await navigator.clipboard.writeText($('yamlText').value); $('copyYaml').textContent='Copied!'; setTimeout(()=>$('copyYaml').textContent='Copy YAML',1000);}}; $('downloadYamlModal').onclick=()=>dl('qr.yaml',new Blob([$('yamlText').value],{{type:'text/yaml'}})); $('yamlModal').addEventListener('click',e=>{{if(e.target===$('yamlModal')) $('yamlModal').classList.remove('open');}}); set(INITIAL_SPEC); render();
</script></body></html>'''

class H(BaseHTTPRequestHandler):
    def sendb(self,status,body,ctype):
        self.send_response(status)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(body)
    def js(self,o,status=200): self.sendb(status,json.dumps(o).encode(),'application/json; charset=utf-8')
    def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))).decode() or '{}')
    def do_GET(self):
        p=urllib.parse.urlparse(self.path).path
        if p in ['/','/index.html']:
            return self.sendb(200, static_html(normalize_spec(DEFAULT_SPEC)).encode('utf-8'),'text/html; charset=utf-8')
        if p=='/api/defaults': return self.js(normalize_spec(DEFAULT_SPEC))
        self.sendb(404,b'not found','text/plain')
    def do_POST(self):
        try:
            p=urllib.parse.urlparse(self.path).path; d=self.body()
            if p=='/api/render':
                r=render_outputs(d.get('spec',d),HERE,2)
                return self.js({'svg':r['svg'],'validation_ok':r['validation_ok'],'validation_message':r['validation_message'],'spec':r['spec']})
            if p=='/api/autotune':
                tuned=autotune_spec(d.get('spec',d), HERE, 2)
                if tuned['best_render'] is None:
                    return self.js({'error':'no valid candidate found','attempts':tuned['attempts']},400)
                return self.js({'svg':tuned['best_render']['svg'],'validation_ok':tuned['best_render']['validation_ok'],'validation_message':tuned['best_render']['validation_message'],'spec':tuned['best_spec'],'best_score':round(playfulness_score(tuned['best_spec']),4),'attempts_tried':len(tuned['attempts'])})
            if p=='/api/png':
                r=render_outputs(d.get('spec',d),HERE,float(d.get('scale',3)))
                return self.sendb(200,r['png'],'image/png')
            if p=='/api/yaml/parse': return self.js({'spec':normalize_spec(yaml.safe_load(d.get('yaml','')) or {})})
            if p=='/api/yaml/dump': return self.js({'yaml':yaml.safe_dump(d.get('spec',{}),sort_keys=False,allow_unicode=True)})
            self.sendb(404,b'not found','text/plain')
        except Exception as e:
            self.js({'error':str(e)},400)
    def log_message(self,fmt,*args): print('[qr-config]',fmt%args)

def serve(host='127.0.0.1',port=8765):
    s=ThreadingHTTPServer((host,port),H)
    print(f'QR configurator: http://{host}:{port}/')
    try: s.serve_forever()
    except KeyboardInterrupt: pass

def main():
    ap=argparse.ArgumentParser(description='QR visual configurator: localhost server or self-contained HTML builder')
    sub=ap.add_subparsers(dest='cmd')
    ps=sub.add_parser('serve',help='run localhost configurator'); ps.add_argument('--host',default='127.0.0.1'); ps.add_argument('--port',type=int,default=8765)
    pb=sub.add_parser('build',help='build self-contained offline configurator HTML'); pb.add_argument('--config',type=Path); pb.add_argument('-o','--output',type=Path,default=Path('qr-configurator.html'))
    a=ap.parse_args()
    if a.cmd=='build':
        spec=load_config(a.config)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(static_html(spec),encoding='utf-8')
        print(a.output)
        return
    if a.cmd=='serve': return serve(a.host,a.port)
    return serve()

if __name__=='__main__': main()
