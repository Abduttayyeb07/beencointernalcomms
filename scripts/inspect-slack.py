"""Read-only Slack reference capture through the user-approved isolated browser.

Never reads cookies/storage or submits forms. Screenshots and DOM-derived style
measurements are local, ignored artifacts; they may contain workspace content.
"""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "clone-workspace/slack/01-recon"
PROBE = """() => {
  const props = ['color','backgroundColor','backgroundImage','fontFamily','fontSize',
    'fontWeight','lineHeight','letterSpacing','paddingTop','paddingRight','paddingBottom',
    'paddingLeft','marginTop','marginBottom','borderRadius','borderTopColor','borderTopWidth',
    'boxShadow','display','position','gridTemplateColumns','gridTemplateRows','gap','width','height'];
  const visible = e => {const r=e.getBoundingClientRect();return r.width>0&&r.height>0};
  const nodes = [...document.querySelectorAll('body *')].filter(visible);
  const signatures = new Map();
  for (const e of nodes) {
    const s=getComputedStyle(e), styles=Object.fromEntries(props.map(k=>[k,s[k]]));
    const key=JSON.stringify(styles); const r=e.getBoundingClientRect();
    if(signatures.has(key)){signatures.get(key).count++;continue;}
    signatures.set(key,{tag:e.tagName,classes:typeof e.className==='string'?e.className:'',
      qa:e.getAttribute('data-qa'),rect:{x:r.x,y:r.y,width:r.width,height:r.height},styles,count:1});
  }
  const controls = [...document.querySelectorAll('button,a[href],[role=button],[role=tab],[role=treeitem],[role=menuitem],input,[contenteditable=true]')]
    .filter(visible).map(e=>({tag:e.tagName,role:e.getAttribute('role'),
      label:e.getAttribute('aria-label')||e.getAttribute('data-placeholder')||e.innerText?.slice(0,100),
      qa:e.getAttribute('data-qa'),classes:typeof e.className==='string'?e.className:'',
      href:e.tagName==='A'?e.getAttribute('href'):null}));
  const media=new Set();let inaccessibleSheets=0;
  function walk(rules){for(const r of rules){if(r.conditionText)media.add(r.conditionText);if(r.cssRules)walk(r.cssRules)}}
  for(const s of document.styleSheets){try{walk(s.cssRules)}catch{inaccessibleSheets++}}
  return {url:location.origin+location.pathname,viewport:{width:innerWidth,height:innerHeight},
    bodyClasses:document.body.className,controls,archetypes:[...signatures.values()],
    media:[...media],inaccessibleSheets,
    fonts:[...document.fonts].filter(f=>f.status==='loaded').map(f=>({family:f.family,weight:f.weight,style:f.style}))};
}"""

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('name')
    parser.add_argument('--width',type=int,default=1920)
    parser.add_argument('--height',type=int,default=1080)
    parser.add_argument('--action',choices=['click','focus','hover'])
    parser.add_argument('--selector')
    parser.add_argument('--label',help='Exact accessible name of a button')
    parser.add_argument('--escape',action='store_true')
    parser.add_argument('--dark',action='store_true')
    parser.add_argument('--expect',help='Wait for a revealed UI selector before capture')
    args=parser.parse_args()
    if not args.name.replace('-','').isalnum():
        parser.error('name must be an alphanumeric slug')
    ROOT.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp('http://127.0.0.1:9227')
        page=next(page for c in browser.contexts for page in c.pages if 'app.slack.com/client/' in page.url)
        page.set_viewport_size({'width':args.width,'height':args.height})
        if args.dark:
            page.emulate_media(color_scheme='dark')
        if args.escape:
            page.keyboard.press('Escape')
        if args.action:
            if not args.selector and not args.label:
                parser.error('--action requires --selector or --label')
            target=page.get_by_role('button',name=args.label,exact=True) if args.label else page.locator(args.selector)
            getattr(target.first,args.action)(timeout=5000)
        if args.expect:
            page.locator(args.expect).first.wait_for(state='visible',timeout=15000)
        page.evaluate('document.fonts.ready.then(()=>true)')
        page.wait_for_timeout(1800)
        data=page.evaluate(PROBE)
        path=ROOT / f'{args.name}.json'
        path.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
        shots=ROOT / 'screenshots'
        shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots / f'{args.name}.png'),full_page=True)
        print(json.dumps({'file':str(path),'archetypes':len(data['archetypes']),
            'controls':len(data['controls']),'last_controls':data['controls'][-8:]},ensure_ascii=False))

if __name__=='__main__':
    main()
