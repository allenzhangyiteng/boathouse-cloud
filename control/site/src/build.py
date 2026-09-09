#!/usr/bin/env python3
"""Assemble control/site/index.html from src/index.template.html: inline logo SVGs and a content-hashed stylesheet URL.
Run: python3 control/site/src/build.py"""
import hashlib, pathlib
here = pathlib.Path(__file__).parent
exec(open(here/'brand_snippets.py').read())
SYM = '<g><polygon points="0,224 164,400 408,404 524,386" fill="{o}"/><polygon points="24,220 334,184 304,296" fill="{f}"/><polygon points="352,200 420,272 366,312 316,294" fill="{p}"/><polygon points="368,176 772,0 440,258" fill="{s}"/><polygon points="772,0 554,368 400,324" fill="{o}"/></g>'
def symbol(w, o="#307BF9", s="#8CB5FB", f="#79AAFB", p="#AECFFD"):
    return f'<svg width="{w}" height="{round(w*404/772)}" viewBox="0 0 772 404" aria-hidden="true" focusable="false">{SYM.format(o=o,s=s,f=f,p=p)}</svg>'
def wordmark_outline():
    """The giant outlined wordmark at the foot of the page: three strokes stacked as a glow, one travelling light."""
    g = 'transform="matrix(0.13697 0 0 -0.15842 -16.98373 236.0396)" fill="none" stroke-linejoin="round" stroke-linecap="round"'
    return (f'<svg class="giant-mark" viewBox="0 0 1448 240" preserveAspectRatio="xMidYMin meet" aria-hidden="true" focusable="false"><g {g}>'
            f'<path d="{WORDMARK_D}" stroke="#307BF9" stroke-opacity=".16" stroke-width="16" vector-effect="non-scaling-stroke"/>'
            f'<path d="{WORDMARK_D}" stroke="#4F92FF" stroke-opacity=".32" stroke-width="5" vector-effect="non-scaling-stroke"/>'
            f'<path d="{WORDMARK_D}" stroke="#8CB5FB" stroke-opacity=".9" stroke-width="1.25" vector-effect="non-scaling-stroke"/>'
            f'<path class="travel" d="{WORDMARK_D}" stroke="#FFFFFF" stroke-width="2.2" vector-effect="non-scaling-stroke" stroke-dasharray="520 5200"/>'
            f'</g></svg>')
def wordmark(h, ink="#14263F"):
    return f'<svg width="{round(h*1448/240)}" height="{h}" viewBox="0 0 1448 240" role="img" aria-label="Boat House"><g fill="{ink}" transform="matrix(0.13697 0 0 -0.15842 -16.98373 236.0396)"><path d="{WORDMARK_D}"/></g></svg>'
site = here.parent
css_hash = hashlib.md5((site/'site.css').read_bytes()).hexdigest()[:8]
import json
marks = json.load(open(site/'brand'/'vendors'/'marks.json'))
for src_name, out_name in (('index.template.html', 'index.html'), ('demo.template.html', 'demo.html'), ('home2.template.html', 'home2.html')):
  t = (here/src_name).read_text()
  for k, v in marks.items():
      t = t.replace('{{MARK_' + k + '}}', v)
  import re
  INKS = {"": "#14263F", "_WHITE": "#F4F8FD", "_MUTED": "#6A81A4", "_OCEAN": "#307BF9", "_BODY": "#425466"}
  def sym(m):
      n, var = int(m.group(1)), m.group(2) or ""
      if var == "_WHITE": return symbol(n, "#FFFFFF", "#DCE9FF", "#C7DBFF", "#EEF4FF")
      if var in ("_MUTED", "_BODY"): c = INKS[var]; return symbol(n, c, c, c, c)
      return symbol(n)
  t = re.sub(r"\{\{SYMBOL_(\d+)(_[A-Z]+)?\}\}", sym, t)
  t = re.sub(r"\{\{WORDMARK_(\d+)(_[A-Z]+)?\}\}", lambda m: wordmark(int(m.group(1)), INKS[m.group(2) or ""]), t)
  t = t.replace('{{WORDMARK_OUTLINE}}', wordmark_outline())
  t = t.replace('/site/site.css', f'/site/site.css?v={css_hash}')
  assert '{{' not in t
  (site/out_name).write_text(t)
  print(out_name, 'built, css', css_hash)
