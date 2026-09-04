#!/usr/bin/env python3
import argparse,json,sys,yaml
from pathlib import Path
from renderer import render_outputs

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('config',type=Path); ap.add_argument('-o','--output',type=Path); ap.add_argument('--png-scale',type=float,default=2); ap.add_argument('--no-png',action='store_true'); ap.add_argument('--allow-unreadable',action='store_true'); a=ap.parse_args()
    txt=a.config.read_text(encoding='utf-8'); spec=json.loads(txt) if a.config.suffix.lower()=='.json' else (yaml.safe_load(txt) or {}); r=render_outputs(spec,a.config.parent,a.png_scale)
    out=a.output or a.config.with_suffix(''); svg=out if out.suffix.lower()=='.svg' else Path(str(out)+'.svg'); stem=svg.with_suffix(''); png=Path(str(stem)+'.png'); meta=Path(str(stem)+'.qr.json'); svg.parent.mkdir(parents=True,exist_ok=True); svg.write_text(r['svg'],encoding='utf-8');
    if not a.no_png: png.write_bytes(r['png'])
    meta.write_text(json.dumps({'value':r['spec']['value'],'validation_ok':r['validation_ok'],'validation_message':r['validation_message'],'svg':str(svg),'png':None if a.no_png else str(png),'spec':r['spec']},indent=2),encoding='utf-8')
    print(svg); print('' if a.no_png else png); print(meta); print(f"validation: {'OK' if r['validation_ok'] else 'FAIL'} - {r['validation_message']}")
    return 0 if r['validation_ok'] or a.allow_unreadable else 2
if __name__=='__main__': raise SystemExit(main())
