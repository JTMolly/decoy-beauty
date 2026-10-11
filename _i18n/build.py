# 官網多語版產生器：從繁中原頁（data-zh／data-en 雙語標記）產生 en/ 英文版和 zh-cn/ 簡體版。
# 用法：~/Claude/Decoy/_工具_截圖/.venv-i18n/bin/python _i18n/build.py [--check]
#   需要 opencc-python-reimplemented、beautifulsoup4。改了任何原頁之後都要重跑，再一起 commit。
#   --check：只列出英文版還沒翻的中文，不寫檔。
import json, os, re, sys
from bs4 import BeautifulSoup, Comment, NavigableString
from opencc import OpenCC
from bs4.formatter import HTMLFormatter
from bs4.dammit import EntitySubstitution

class KeepOrder(HTMLFormatter):
    """輸出時保留原本的屬性順序、不加 />，讓產生的頁面和原頁長得一樣"""
    def attributes(self, tag):
        return list(tag.attrs.items()) if tag.attrs else []
FMT = KeepOrder(entity_substitution=EntitySubstitution.substitute_xml, void_element_close_prefix='')
def html_of(soup): return soup.decode(formatter=FMT)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = 'https://decoy-beauty.com/'
CFG = json.load(open(os.path.join(ROOT, '_i18n', 'pages.json'), encoding='utf-8'))
PAGES = CFG['pages']                       # {檔名: {"en": true/false, "zh-cn": true/false}}
META_EN = CFG['meta_en']                   # {檔名: {title, description, og:title, og:description}}
cc = OpenCC('tw2s')
CJK = re.compile(r'[㐀-鿿]')
LANGS = [('zh-Hant', '繁', ''), ('en', 'EN', 'en/'), ('zh-Hans', '简', 'zh-cn/')]
VAR = {'en': 'en/', 'zh-cn': 'zh-cn/'}

def has(page, lang):
    return lang == 'zh-Hant' or PAGES.get(page, {}).get({'en': 'en', 'zh-Hans': 'zh-cn'}[lang], False)

def switcher_html(page, cur):
    out = []
    for code, label, pre in LANGS:
        if not has(page, code): continue
        cls = ' class="on" aria-current="true"' if code == cur else ''
        out.append(f'<a href="{pre}{page}" hreflang="{code}" lang="{code}" data-lang="{code}"{cls}>{label}</a>')
    return '<div class="lang-switch" aria-label="Language">' + ''.join(out) + '</div>'

def alt_links(page):
    url = lambda pre: SITE + pre + ('' if page == 'index.html' else page)
    links = [f'<link rel="alternate" hreflang="{code}" href="{url(pre)}">' for code, _, pre in LANGS if has(page, code)]
    links.append(f'<link rel="alternate" hreflang="x-default" href="{url("")}">')
    return '<!-- i18n:alt -->' + ''.join(links) + '<!-- /i18n:alt -->'

def set_alt_and_canonical(html, page, pre):
    # 重跑要一模一樣：先連同前面的換行一起拿掉，再放回去
    html = re.sub(r'\n?<!-- i18n:alt -->.*?<!-- /i18n:alt -->', '', html, flags=re.S)
    html = re.sub(r'\n?<link rel="canonical"[^>]*>', '', html)
    html = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+</head>', '\n\n</head>', html)   # 舊版留下的多餘空行
    canon = SITE + pre + ('' if page == 'index.html' else page)
    return html.replace('</head>', f'<link rel="canonical" href="{canon}">\n{alt_links(page)}\n</head>', 1)

def set_switcher(html, page, cur):
    new, n = re.subn(r'<div class="lang-switch"[^>]*>.*?</div>', switcher_html(page, cur), html, count=1, flags=re.S)
    assert n == 1, ('no switcher', page)
    return new

def rewrite_links(soup, page, pre):
    """變體頁加 <base href="../">：圖檔、影片照舊指向根目錄；有翻譯的頁面連到同語言版。"""
    for a in soup.find_all(href=True):
        if a.name == 'link' or a.get('data-lang'): continue
        h = a['href']
        if re.match(r'^(https?:|mailto:|tel:|data:|//)', h): continue
        if h.startswith('#'): a['href'] = pre + page + h; continue
        m = re.match(r'^(\./|/)?([\w-]+\.html)?([?#].*)?$', h)
        if not m: continue
        target = m.group(2) or ('index.html' if h in ('/', './', '') or h.startswith(('/#', './#', '/?')) else None)
        if not target: continue
        lang = 'en' if pre == 'en/' else 'zh-Hans'
        if target in PAGES and not has(target, lang) and a.find_parent('nav') and a.parent.name == 'li':
            a.parent.decompose(); continue   # 選單裡還沒有這個語言的頁（例如成分小教室英文版），先不放
        if target in PAGES and has(target, lang):
            a['href'] = pre + ('' if target == 'index.html' else target) + (m.group(3) or '')
        else:
            a['href'] = (target if target != 'index.html' else './') + (m.group(3) or '')

def leftover_cjk(soup):
    found = []
    for t in soup.find_all(string=True):
        if isinstance(t, Comment) or t.parent.name in ('script', 'style', 'noscript'): continue
        if t.parent.name == 'title': continue
        if t.find_parent(attrs={'lang': True}) and t.find_parent(attrs={'lang': True}).name != 'html': continue   # 刻意保留的中文（標 lang）
        if CJK.search(t): found.append(' '.join(t.split())[:80])
    for el in soup.find_all(True):
        for attr in ('alt', 'title', 'aria-label', 'placeholder'):
            if el.get(attr) and CJK.search(el[attr]): found.append(f'[{attr}] {el[attr][:80]}')
    return found

HOLD = CFG.get('hold', [])   # 例如 ["patent"]：中文說法還沒定案的句子，英文版和簡體版先不放（繁中照舊）
def drop_held(soup):
    for el in soup.select('[data-hold]'):
        if el.attrs is not None and el.get('data-hold') in HOLD: el.decompose()   # 外層先拿掉的，裡面的就跳過

def make_en(page, src):
    soup = BeautifulSoup(src, 'html.parser')
    drop_held(soup)
    for el in soup.select('[data-zh]'): el.decompose()
    for el in soup.find_all(True):   # data-en-aria-label="…" 之類：英文版換成該屬性
        for k in [k for k in el.attrs if k.startswith('data-en-')]:
            el[k[8:]] = el[k]; del el[k]
    for el in soup.select('[data-en]'):
        del el['data-en']
        st = el.get('style', '')
        st = re.sub(r'display\s*:\s*none\s*;?', '', st).strip()
        if st: el['style'] = st
        elif el.has_attr('style'): del el['style']
    return soup

def build(page, check=False):
    path = os.path.join(ROOT, page)
    src = open(path, encoding='utf-8').read()
    src = set_switcher(src, page, 'zh-Hant')
    src = set_alt_and_canonical(src, page, '')
    if not check: open(path, 'w', encoding='utf-8').write(src)
    report = {}
    if has(page, 'en'):
        soup = make_en(page, src)
        report['en'] = leftover_cjk(soup)
        if not check:
            rewrite_links(soup, page, 'en/')
            html = html_of(soup)
            html = html.replace('<html lang="zh-TW">', '<html lang="en">').replace('<html lang="zh-Hant">', '<html lang="en">')
            html = re.sub(r'<body([^>]*)class="([^"]*)"', lambda m: f'<body{m.group(1)}class="{m.group(2)} en"', html, count=1) if re.search(r'<body[^>]*class=', html) else html.replace('<body', '<body class="en"', 1)
            html = set_switcher(html, page, 'en')
            html = set_alt_and_canonical(html, page, 'en/')
            html = html.replace('<head>', '<head>\n<base href="../">', 1)
            m = META_EN.get(page, {})
            if 'title' in m: html = re.sub(r'<title>.*?</title>', f'<title>{m["title"]}</title>', html, count=1, flags=re.S)
            for k in ('description', 'keywords'):
                if k in m: html = re.sub(rf'<meta name="{k}" content="[^"]*">', f'<meta name="{k}" content="{m[k]}">', html)
            for k in ('og:title', 'og:description'):
                if k in m: html = re.sub(rf'<meta property="{k}" content="[^"]*">', f'<meta property="{k}" content="{m[k]}">', html)
            write(os.path.join(ROOT, 'en', page), html)
    if has(page, 'zh-Hans') and not check:
        soup = BeautifulSoup(src, 'html.parser')
        drop_held(soup)
        for el in soup.select('[data-en]'): el.decompose()
        rewrite_links(soup, page, 'zh-cn/')
        html = cc.convert(html_of(soup))
        html = re.sub(r'<html lang="zh-(TW|Hant)">', '<html lang="zh-Hans">', html)
        html = html.replace('Noto+Serif+TC', 'Noto+Serif+SC').replace('Noto Serif TC', 'Noto Serif SC').replace('Noto+Sans+TC', 'Noto+Sans+SC').replace('Noto Sans TC', 'Noto Sans SC')
        html = set_switcher(html, page, 'zh-Hans')
        html = set_alt_and_canonical(html, page, 'zh-cn/')
        html = html.replace('<head>', '<head>\n<base href="../">', 1)
        write(os.path.join(ROOT, 'zh-cn', page), html)
    return report

def write(path, html):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8').write(html)

if __name__ == '__main__':
    check = '--check' in sys.argv
    for page in PAGES:
        r = build(page, check)
        for line in r.get('en', []): print(f'{page}\tEN 未翻\t{line}')
    print('done', 'check' if check else 'built')
