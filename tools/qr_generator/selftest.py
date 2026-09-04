#!/usr/bin/env python3
from renderer import render_outputs

def main():
    styles=[
        {'modules':{'shape':'rounded','scale':.88,'color':'#202124'},'eyes':{'frame_shape':'rounded','pupil_shape':'circle','color':'#F58220','pupil_color':'#F58220'}},
        {'modules':{'shape':'circle','scale':.90,'color':'#202124'},'eyes':{'frame_shape':'rounded','pupil_shape':'circle','color':'#7B2494','pupil_color':'#7B2494'}},
        {'modules':{'shape':'diamond','scale':.95,'color':'#202124'},'eyes':{'frame_shape':'rounded','pupil_shape':'square','color':'#52BEC1','pupil_color':'#52BEC1'}},
    ]
    for i,style in enumerate(styles,1):
        spec={'value':'https://example.com/test','error_correction':'H','quiet_zone':4,'background':{'color':'#FFFFFF'},'logo':{'enabled':False},'output':{'validate':True},**style}
        r=render_outputs(spec,png_scale=3)
        print(i,r['validation_ok'],r['validation_message'])
        if not r['validation_ok']: return 2
    return 0
if __name__=='__main__': raise SystemExit(main())
