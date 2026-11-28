# ruff: noqa
# Vendored with minimal changes (cache dir, urllib fallback); kept in the original style so it
# can be diffed against LabelPortable/page_context.py.
"""Read a source page like a browser and pull out what it says about one image on it.

Vendored from the labelling pipeline (Handoff LabelPortable/page_context.py, which is not in git)
so that the /admin CSV import parses pages exactly like the corpus was parsed:

- fetched with curl_cffi impersonating Chrome when it is installed, else with urllib and the same
  browser headers; per-host spacing, Retry-After respected. No CAPTCHA or login bypass, SSL is
  verified: a page that blocks bots gets status 'blocked'.
- only public IP addresses are contacted (checked again on every redirect).
- the parsed result (never the raw HTML) is cached as JSON under ``CACHE`` (see set_cache()).
- image_context() finds the <img> of the image URL and its alt/title/caption, the nearby text and
  the page's product name (JSON-LD), h1 and title, and compares them with the entity name.
"""
import hashlib
import html as htmllib
import ipaddress
import json
import random
import re
import socket
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

CACHE = Path('data/admin/pages')
MAX_BYTES = 3_000_000
MAX_IMAGES = 600
VERSION = 2
HEADERS = {'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
           'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7', 'Upgrade-Insecure-Requests': '1'}
BLOCK_MARKERS = ('just a moment', 'attention required', 'access denied', 'verify you are human', 'are you a robot',
                 'security check', 'checking your browser', 'pardon our interruption', 'robot check', 'captcha',
                 'ddos-guard', 'please enable javascript and cookies', 'request unsuccessful', 'bot verification',
                 'xác minh bạn là con người', 'truy cập bị từ chối', 'one moment, please', 'please wait while',
                 'just a second', 'checking if the site connection is secure', 'enable javascript to continue')


def norm(value):
    value = unicodedata.normalize('NFD', str(value or '').lower().replace('đ', 'd'))
    return re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


def clip(text, n):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


def set_cache(path):
    """Where parsed pages are cached (one JSON per URL)."""
    global CACHE
    CACHE = Path(path)


def cache_path(url):
    return CACHE / (hashlib.sha1(url.encode('utf-8')).hexdigest()[:24] + '.json')


def load(url):
    p = cache_path(url)
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
            if data.get('v') == VERSION:
                return data
        except ValueError:
            pass
    return None


def save(url, data):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = cache_path(url)
    tmp = p.with_suffix(f'.{threading.get_ident()}.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(p)


# ---------------------------------------------------------------------------------------------- fetching
def check_public(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('chỉ URL HTTP(S) công khai')
    if (parts.port or (443 if parts.scheme == 'https' else 80)) not in (80, 443):
        raise ValueError('cổng không hỗ trợ')
    infos = socket.getaddrinfo(parts.hostname, None, type=socket.SOCK_STREAM)
    if not infos or any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
        raise ValueError('địa chỉ nội bộ/mạng riêng')
    return parts.hostname.lower()


class Polite:
    """One request per host every `interval` s (shared by all threads, with jitter); Retry-After/429 cool a host."""
    def __init__(self, interval=2.0):
        self.interval, self.lock, self.next_ok = interval, threading.Lock(), {}

    def wait(self, host):
        while True:
            with self.lock:
                t, at = time.monotonic(), self.next_ok.get(host, 0)
                if t >= at:
                    self.next_ok[host] = t + self.interval * random.uniform(0.8, 1.4)
                    return
            time.sleep(min(at - t, 2))

    def cool(self, host, seconds):
        with self.lock:
            self.next_ok[host] = max(self.next_ok.get(host, 0), time.monotonic() + seconds)


_local = threading.local()


USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/131.0.0.0 Safari/537.36')


class _UrllibResponse:
    """The part of a curl_cffi response that get() and fetch_image() use."""

    def __init__(self, raw):
        self.status_code = getattr(raw, 'status', None) or raw.getcode()
        self.headers = {k.lower(): v for k, v in raw.headers.items()}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # get() follows redirects itself, checking every hop


class _UrllibSession:
    """Fallback when curl_cffi is not installed: urllib with browser headers, no redirects."""

    def __init__(self):
        self.opener = urllib.request.build_opener(_NoRedirect())

    def get(self, url, headers=None, timeout=25, allow_redirects=False, content_callback=None):
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, **(headers or {})})
        try:
            raw = self.opener.open(request, timeout=timeout[0] if isinstance(timeout, tuple) else timeout)
        except urllib.error.HTTPError as error:  # 3xx/4xx/5xx still carry headers and a body
            raw = error
        try:
            response = _UrllibResponse(raw)
            while chunk := raw.read(65536):
                if content_callback(chunk) == _ABORT:
                    break
            return response
        finally:
            raw.close()


def session():
    if not hasattr(_local, 's'):
        try:
            from curl_cffi import requests as cr
            _local.s = cr.Session(impersonate='chrome')
        except ImportError:
            _local.s = _UrllibSession()
    return _local.s


_ABORT = 0xFFFFFFFF  # CURL_WRITEFUNC_ERROR; any other return value does not stop libcurl
TIMEOUT = (25, 35)  # curl_cffi: 25 s to connect, 25 + 35 = 60 s for the whole request


def download(url, headers, max_bytes):
    """One request, no redirects followed, keeping at most max_bytes of the body.

    -> (response, body, truncated). Never use curl_cffi's stream=True here: it uses one curl
    handle from two threads, and with the import's fetch threads it corrupted the heap and
    killed the API process.
    """
    body, truncated = bytearray(), False

    def write(chunk):
        nonlocal truncated
        room = max_bytes - len(body)
        body.extend(chunk[:room])
        if len(chunk) <= room:
            return len(chunk)
        truncated = True
        return _ABORT

    try:
        response = session().get(url, headers=headers, timeout=TIMEOUT, allow_redirects=False,
                                 content_callback=write)
    except Exception as error:
        response = getattr(error, 'response', None)
        if not truncated or response is None:  # not the abort above: a real failure
            raise
    return response, bytes(body), truncated


def get(url, polite, referer=None):
    """-> (http_status, final_url, content_type, body bytes ≤ MAX_BYTES, headers)."""
    for _ in range(6):
        host = check_public(url)
        polite.wait(host)
        headers = dict(HEADERS)
        if referer:
            headers['Referer'] = referer
        r, body, _ = download(url, headers, MAX_BYTES)  # long page: parse what we have instead of failing
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get('location'):
            url = urljoin(url, r.headers.get('location'))
            continue
        return r.status_code, url, (r.headers.get('content-type') or '').lower(), body, dict(r.headers)
    raise ValueError('quá nhiều chuyển hướng')


IMAGE_HEADERS = {'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                 'Accept-Language': 'vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7',
                 'Sec-Fetch-Dest': 'image', 'Sec-Fetch-Mode': 'no-cors', 'Sec-Fetch-Site': 'cross-site'}


def fetch_image(url, max_bytes=15_000_000, referer=None):
    """Download an image like Chrome does (fallback when the plain fetcher is refused: 415/403/timeouts).
    Public-IP check on every redirect hop, SSL verified, no challenge solving. Errors keep the
    'HTTP nnn' / 'timed out' wording so polite_fetch's retry logic classifies them the same way.
    -> (bytes, final_url, content_type)"""
    for _ in range(6):
        check_public(url)
        headers = dict(IMAGE_HEADERS)
        if referer:
            headers['Referer'] = referer
        try:
            r, body, truncated = download(url, headers, max_bytes)
        except Exception as error:
            text = str(error)
            raise ValueError(('timed out: ' if '(28)' in text or 'timed out' in text.lower() else 'chrome: ') + text[:200])
        if r.status_code in (301, 302, 303, 307, 308) and r.headers.get('location'):
            url = urljoin(url, r.headers.get('location'))
            continue
        if r.status_code != 200:
            raise ValueError('HTTP ' + str(r.status_code))
        kind = (r.headers.get('content-type') or '').lower()
        if not kind.startswith('image/'):
            raise ValueError('URL không trả về ảnh')
        if truncated:
            raise ValueError('Nội dung vượt giới hạn tải')
        return body, url, kind
    raise ValueError('Quá nhiều chuyển hướng')


def decode(body, ctype):
    m = re.search(r'charset=["\']?([\w-]+)', ctype) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I)
    enc = m.group(1) if m else 'utf-8'
    enc = enc.decode('ascii', 'ignore') if isinstance(enc, bytes) else enc
    try:
        return body.decode(enc, errors='replace')
    except LookupError:
        return body.decode('utf-8', errors='replace')


def looks_blocked(status, text):
    head = text[:20000].lower()
    title = (re.search(r'<title[^>]*>(.*?)</title>', head, re.S) or [None, ''])[1]
    if any(m in title for m in BLOCK_MARKERS):
        return True
    if status in (401, 403, 202, 503) and (len(text) < 60000 or any(m in head for m in BLOCK_MARKERS)):
        return True
    return status == 200 and len(re.sub(r'<[^>]+>', ' ', text)) < 1500 and any(m in head for m in BLOCK_MARKERS)


# ------------------------------------------------------------------ connectivity
# A network outage must not turn into "image failed" / "page failed" / "sheet skipped": callers wait here instead.
_NET = {'at': 0.0, 'ok': True, 'waiting': False}
_NET_LOCK = threading.Lock()
NET_PROBES = (('1.1.1.1', 443), ('8.8.8.8', 53), ('9.9.9.9', 443))  # plain TCP connects, nothing is sent


def online(ttl=5.0):
    """True when this machine can reach the Internet (cached for ttl seconds, shared by all threads)."""
    with _NET_LOCK:
        if time.time() - _NET['at'] < ttl:
            return _NET['ok']
        ok = False
        for host, port in NET_PROBES:
            try:
                socket.create_connection((host, port), 3).close()
                ok = True
                break
            except OSError:
                continue
        _NET.update(at=time.time(), ok=ok)
        return ok


def wait_online(log=None, stopped=None, poll=15):
    """Block while there is no Internet. Returns True if it had to wait (the caller should retry its request)."""
    if online():
        return False
    with _NET_LOCK:
        first, _NET['waiting'] = not _NET['waiting'], True
    if first and log:
        log('MẤT MẠNG: tạm dừng, chờ có mạng lại rồi làm tiếp (không tính là lỗi)')
    t0 = time.time()
    while not online(ttl=poll - 1):
        if stopped and stopped():
            break
        time.sleep(poll)
    with _NET_LOCK:
        last, _NET['waiting'] = _NET['waiting'], False
    if last and log:
        log(f'CÓ MẠNG LẠI sau {int(time.time() - t0)} giây: chạy tiếp')
    return True


def fetch_page(url, polite, retries=2):
    """Fetch + parse one page (cached). Statuses: ok | blocked | not_found | rate_limited | not_html | failed."""
    cached = load(url)
    if cached and cached.get('status') in ('ok', 'blocked', 'not_found', 'not_html'):
        return cached
    out = {'v': VERSION, 'url': url, 'fetched_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    for attempt in range(retries + 1):
        try:
            status, final, ctype, body, headers = get(url, polite)
        except Exception as error:  # DNS, TLS, timeout, private address ...
            msg = str(error)
            out.update(status='failed', error=clip(msg, 200))
            if attempt < retries and re.search(r'timed out|timeout|\(28\)|\(56\)|\(35\)', msg):
                time.sleep(3 * (attempt + 1))
                continue
            break
        out.update(http=status, final_url=final)
        if status == 429 or (status == 503 and 'retry-after' in {k.lower() for k in headers}):
            ra = next((v for k, v in headers.items() if k.lower() == 'retry-after'), '')
            wait = int(ra) if str(ra).isdigit() else 20 * (attempt + 1)
            polite.cool(urlsplit(final).hostname or '', min(wait, 120))
            out.update(status='rate_limited', error=f'HTTP {status}')
            if attempt < retries and wait <= 120:
                time.sleep(min(wait, 120))
                continue
            break
        if status in (404, 410):
            out.update(status='not_found', error=f'HTTP {status}')
            break
        if 'html' not in ctype and 'xml' not in ctype and ctype:
            out.update(status='not_html', error=ctype[:60])
            break
        text = decode(body, ctype)
        if looks_blocked(status, text):
            out.update(status='blocked', error=f'HTTP {status}: trang chặn bot/CAPTCHA')
            break
        if status != 200:
            out.update(status='failed', error=f'HTTP {status}')
            if attempt < retries and status in (500, 502, 504):
                time.sleep(5 * (attempt + 1))
                continue
            break
        try:
            out.update(status='ok', error='', **parse(text, final))
        except Exception as error:  # a parser bug must not make every image of this page fetch it again
            out.update(status='failed', error=clip('lỗi phân tích trang: ' + str(error), 200))
        break
    save(url, out)
    return out


# ---------------------------------------------------------------------------------------------- parsing
IMG_ATTRS = ('src', 'data-src', 'data-original', 'data-lazy-src', 'data-lazyload', 'data-zoom-image', 'data-large_image',
             'data-full', 'data-src-large', 'data-image', 'data-hi-res-src', 'data-old-hires', 'data-a-hires')
SRCSET_ATTRS = ('srcset', 'data-srcset', 'data-lazy-srcset')
HIDDEN = ('script', 'style', 'template', 'svg', 'nav', 'footer')
BLOCK_TAGS = ('p', 'div', 'article', 'section', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'figcaption', 'figure', 'td', 'tr',
              'table', 'ul', 'ol', 'br', 'span', 'em', 'blockquote', 'header', 'main', 'aside', 'dd', 'dt')
IMG_EXT = re.compile(r'\.(jpe?g|png|webp|gif|avif|bmp)(\?|$)', re.I)


class PageParser(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.hidden = 0
        self.cur = []
        self.events = []          # ('t', text) | ('i', image index)
        self.images = []
        self.title, self.in_title = [], False
        self.h1, self.in_h, self.hbuf, self.heading = [], None, [], ''
        self.meta = {}
        self.ld, self.in_ld = [], False
        self.figures = []         # stack of [figure_id]
        self.fig_caption = {}     # figure_id -> text parts
        self.in_figcap = None
        self.links = []           # stack of hrefs of open <a>
        self.sources = []         # <source srcset> seen inside current <picture>
        self.fig_n = 0

    def flush(self):
        text = ' '.join(' '.join(self.cur).split())
        if len(text) >= 3:
            self.events.append(('t', text))
        self.cur = []

    def url_list(self, attrs):
        urls = []
        for k in IMG_ATTRS:
            v = attrs.get(k)
            if v and not v.startswith('data:'):
                urls.append(v)
        for k in SRCSET_ATTRS:
            for part in (attrs.get(k) or '').split(','):
                u = part.strip().split(' ')[0]
                if u and not u.startswith('data:'):
                    urls.append(u)
        return urls

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag == 'script':
            self.in_ld = 'ld+json' in a.get('type', '').lower()
        if tag in HIDDEN:
            self.hidden += 1
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or a.get('itemprop') or '').lower()
            if key in ('og:title', 'og:image', 'og:image:secure_url', 'og:description', 'description', 'twitter:image', 'twitter:title', 'image'):
                self.meta.setdefault(key, a.get('content', ''))
        if tag in ('h1', 'h2', 'h3', 'h4') and not self.hidden:
            self.flush(); self.in_h, self.hbuf = tag, []
        if tag == 'figure':
            self.fig_n += 1; self.figures.append(self.fig_n)
        if tag == 'figcaption':
            self.flush(); self.in_figcap = self.figures[-1] if self.figures else -1
        if tag == 'a':
            href = a.get('href', '')
            self.links.append(href if IMG_EXT.search(href) else '')
        if tag == 'picture':
            self.sources = []
        if tag == 'source':
            self.sources += self.url_list(a)
        if tag == 'img' and len(self.images) < MAX_IMAGES:
            urls = self.url_list(a) + self.sources + [l for l in self.links if l]
            self.sources = []
            if urls:
                self.flush()
                self.images.append({'urls': [urljoin(self.base, u) for u in dict.fromkeys(urls)][:12], 'alt': clip(a.get('alt'), 300),
                                    'title': clip(a.get('title'), 200), 'fig': self.figures[-1] if self.figures else None,
                                    'heading': self.heading})
                self.events.append(('i', len(self.images) - 1))
        elif tag in BLOCK_TAGS and not self.hidden:
            self.flush()

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        if tag in HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        if tag == 'script':
            self.in_ld = False
        if tag == self.in_h:
            text = clip(' '.join(self.hbuf), 200)
            if text:
                self.heading = text
                if tag == 'h1' and len(self.h1) < 2:
                    self.h1.append(text)
            self.in_h = None
        if tag == 'figcaption':
            self.flush(); self.in_figcap = None
        if tag == 'figure' and self.figures:
            self.figures.pop()
        if tag == 'a' and self.links:
            self.links.pop()
        if tag in BLOCK_TAGS and not self.hidden:
            self.flush()

    def handle_data(self, text):
        if self.in_ld:
            self.ld.append(text)
            return
        if self.in_title:
            self.title.append(text)
        if self.hidden:
            return
        self.cur.append(text)
        if self.in_h:
            self.hbuf.append(text)
        if self.in_figcap is not None:
            self.fig_caption.setdefault(self.in_figcap, []).append(text)


def walk_ld(obj, out):
    if isinstance(obj, list):
        for x in obj:
            walk_ld(x, out)
    elif isinstance(obj, dict):
        kind = obj.get('@type')
        kind = ' '.join(kind) if isinstance(kind, list) else str(kind or '')
        if any(k in kind for k in ('Product', 'ImageObject', 'Article', 'NewsArticle', 'BlogPosting', 'Place', 'Thing', 'Book', 'Drug')):
            brand = obj.get('brand')
            brand = brand.get('name') if isinstance(brand, dict) else brand
            item = {'type': kind[:30], 'name': clip(obj.get('name') or obj.get('headline') or '', 200),
                    'brand': clip(brand if isinstance(brand, str) else '', 60),
                    'model': clip(' '.join(str(obj.get(k) or '') for k in ('sku', 'mpn', 'model') if isinstance(obj.get(k), (str, int))), 80),
                    'caption': clip(obj.get('caption') or '', 200) if 'ImageObject' in kind else ''}
            imgs = obj.get('image') or obj.get('contentUrl') or []
            imgs = imgs if isinstance(imgs, list) else [imgs]
            item['images'] = [(i.get('url') or i.get('contentUrl')) if isinstance(i, dict) else i for i in imgs][:20]
            item['images'] = [i for i in item['images'] if isinstance(i, str)]
            if 'ImageObject' in kind and isinstance(obj.get('contentUrl'), str):  # can be a list/dict: urljoin would raise
                item['images'].append(obj['contentUrl'])
            if item['name'] or item['images']:
                out.append(item)
        for k in ('@graph', 'mainEntity', 'itemListElement', 'offers', 'associatedMedia', 'image'):
            if isinstance(obj.get(k), (list, dict)):
                walk_ld(obj[k], out)


def parse(text, base):
    p = PageParser(base)
    try:
        p.feed(text)
        p.close()
    except Exception:
        pass  # malformed markup: keep what was parsed
    p.flush()
    ld = []
    for chunk in p.ld[:20]:
        try:
            walk_ld(json.loads(chunk.strip()), ld)
        except ValueError:
            continue
    texts = [x[1] for x in p.events if x[0] == 't']
    events = p.events
    images = []
    for pos, (kind, val) in enumerate(events):
        if kind != 'i':
            continue
        im = p.images[val]
        before = next((events[j][1] for j in range(pos - 1, max(-1, pos - 4), -1) if events[j][0] == 't'), '')
        after = next((events[j][1] for j in range(pos + 1, min(len(events), pos + 4)) if events[j][0] == 't'), '')
        cap = ' '.join(p.fig_caption.get(im['fig'], [])) if im['fig'] else ''
        images.append({'urls': im['urls'], 'alt': im['alt'], 'title': im['title'], 'caption': clip(cap, 300),
                       'before': clip(before, 260), 'after': clip(after, 260), 'heading': im['heading']})
    products = [x for x in ld if x['name']][:6]
    ld_images = [{'urls': [urljoin(base, u) for u in x['images']], 'name': x['name'], 'caption': x.get('caption', '')} for x in ld if x['images']][:30]
    return {'title': clip(' '.join(p.title), 200), 'h1': p.h1, 'og_title': clip(p.meta.get('og:title') or p.meta.get('twitter:title'), 200),
            'og_image': urljoin(base, p.meta.get('og:image') or p.meta.get('og:image:secure_url') or p.meta.get('twitter:image') or '') if (p.meta.get('og:image') or p.meta.get('og:image:secure_url') or p.meta.get('twitter:image')) else '',
            'description': clip(p.meta.get('og:description') or p.meta.get('description'), 300),
            'products': products, 'ld_images': ld_images, 'images': images,
            'text_len': sum(len(t) for t in texts), 'text_head': clip(' '.join(texts), 1500)}


# ---------------------------------------------------------------------------------------------- matching
SIZE_TAIL = re.compile(r'([-_.](\d{2,4}x\d{2,4}|\d{2,4}x|x\d{2,4})|@\dx|-scaled|-e\d{9,}|[-_](thumb|thumbnail|small|medium|large|min|big|full|zoom|resized|crop(ped)?)|_\d{2,4})+$')
GENERIC = {'image', 'images', 'img', 'photo', 'default', 'thumb', 'thumbnail', 'logo', 'banner', 'placeholder', 'noimage', 'index', 'main', 'large', 'medium', 'small', 'original'}


def unwrap(url):
    url = htmllib.unescape(unquote(str(url or '').strip()))
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    for k in ('url', 'src', 'u', 'image', 'img'):
        if q.get(k) and re.match(r'^(https?:)?//|^/', q[k][0]):
            return unwrap(q[k][0])
    m = re.search(r'/cdn-cgi/image/[^/]+/(.+)$', parts.path)
    if m:
        return unwrap(m.group(1) if m.group(1).startswith('http') else parts.scheme + '://' + parts.netloc + '/' + m.group(1))
    return url


def img_keys(url):
    """Size-independent keys of an image URL: file stem without size/variant suffixes (+ parent folder when short)."""
    url = unwrap(url)
    path = urlsplit(url if '//' in url else 'http://x/' + url).path.lower()
    segs = [s for s in path.split('/') if s]
    if not segs:
        return set()
    name = segs[-1]
    stem = name.rsplit('.', 1)[0] if '.' in name else name
    stem = stem.split('._')[0]                        # amazon: 71abc._AC_UF1000_
    stem = re.sub(r'(_\d{2,4}x\d{0,4}|_x\d{2,4})(_crop_\w+)?$', '', stem)  # shopify
    stem = SIZE_TAIL.sub('', stem)
    keys = set()
    if len(stem) >= 6 and stem not in GENERIC and not stem.isdigit() or (stem.isdigit() and len(stem) >= 8):
        keys.add(stem)
    if len(segs) >= 2 and (len(stem) < 10 or stem in GENERIC):
        keys.add(segs[-2] + '/' + stem)
    return keys


def find_image(page, image_urls):
    want = set()
    for u in image_urls:
        want |= img_keys(u)
    if not want or not page:
        return None, ''
    for im in page.get('images', []):
        for u in im['urls']:
            if img_keys(u) & want:
                return im, 'img'
    for x in page.get('ld_images', []):
        if any(img_keys(u) & want for u in x['urls']):
            return {'alt': '', 'title': '', 'caption': x.get('caption') or '', 'before': '', 'after': '', 'heading': x.get('name', '')}, 'json-ld'
    if page.get('og_image') and img_keys(page['og_image']) & want:
        return {'alt': '', 'title': '', 'caption': '', 'before': '', 'after': '', 'heading': ''}, 'og:image'
    return None, ''


UNITS = {'mg', 'ml', 'g', 'kg', 'cm', 'mm', 'm', 'l', 'w', 'v', 'gb', 'tb', 'mb', 'oz', 'inch', 'in', 'k', 'x', 'h', 's', 'p', 'ph', 'mah', 'hz', 'km', 'lb', 'lbs', 'vnd', 'd', 'mcg', 'iu', 'pcs', 'no', 'nr', 'v'}
LETTER_CODE = re.compile(r'(?<![a-z0-9])([a-z]{1,5})-?(\d{1,5})([a-z]{0,2})(?![a-z0-9])')
NUMBER_CODE = re.compile(r'(?<![a-z0-9.,])(\d{3,6})(?![0-9.,]|[a-z])')


def codes(text, numbers=True):
    """Model codes: Li-2 → li2, C-47 → c47, FAB-500 → fab500 (+500), MXX-1200, 6204. Units (8mg), prices (350.000),
    sizes (700x408) are not codes. numbers=False keeps only letter codes (for file names full of upload ids)."""
    t = unicodedata.normalize('NFD', str(text or '').lower().replace('đ', 'd')).encode('ascii', 'ignore').decode()
    t = re.sub(r'\d+x\d+', ' ', re.sub(r'[^a-z0-9.,-]+', ' ', t))
    out = set()
    for pre, num, suf in LETTER_CODE.findall(t):
        if pre not in UNITS:
            out.add(pre + num + suf)
    if numbers:
        out |= set(NUMBER_CODE.findall(t))
    return out


def is_year(code):
    return code.isdigit() and len(code) == 4 and code[:2] in ('18', '19', '20')


STOP = {'cua', 'va', 'cho', 'the', 'of', 'and', 'tai', 'o', 'voi', 'loai', 'cai', 'chiec', 'con', 'mot', 'nhung', 'cac', 'a', 'an', 'in', 'for', 'goi', 'hop'}


def coverage(entity, text):
    terms = [t for t in dict.fromkeys(norm(entity).split()) if t not in STOP and len(t) > 1]
    words = set(norm(text).split())
    return (sum(t in words for t in terms) / len(terms)) if terms else 0.0


def image_context(page, image_url, entity, final_url=''):
    """Compact, model-facing evidence for one image + a code-level verdict. Never raises."""
    if not page:
        return {'status': 'missing', 'check': 'chưa đọc trang nguồn'}
    st = page.get('status')
    if st != 'ok':
        why = {'blocked': 'trang chặn bot/CAPTCHA — bạn tự check nguồn', 'not_found': 'trang nguồn 404/không còn',
               'rate_limited': 'trang nguồn giới hạn tốc độ, chưa đọc được', 'not_html': 'nguồn không phải HTML'}.get(st, 'không tải được trang nguồn')
        return {'status': st, 'check': why}
    im, how = find_image(page, [image_url, final_url])
    product = ' / '.join(x['name'] + (f' ({x["brand"]})' if x.get('brand') and norm(x['brand']) not in norm(x['name']) else '') + (f' [{x["model"]}]' if x.get('model') else '')
                         for x in page.get('products', [])[:2] if 'Product' in x['type'] or 'Drug' in x['type'] or 'Book' in x['type'])
    page_name = product or (page.get('h1') or [''])[0] or page.get('og_title') or page.get('title') or ''
    own = ''
    near = ''
    if im:
        bits = []
        if im.get('alt'):
            bits.append(f'alt "{clip(im["alt"], 160)}"')
        if im.get('caption') and norm(im['caption']) != norm(im.get('alt')):
            bits.append(f'chú thích "{clip(im["caption"], 200)}"')
        if im.get('title') and norm(im['title']) != norm(im.get('alt')):
            bits.append(f'title "{clip(im["title"], 100)}"')
        own = '; '.join(bits)
        near = clip(im.get('before') or im.get('after') or '', 180) if not im.get('caption') else ''
    fname = unquote(urlsplit(unwrap(image_url)).path.rsplit('/', 1)[-1]).rsplit('.', 1)[0]
    alt_text = ' '.join(filter(None, [im.get('alt', ''), im.get('caption', ''), im.get('title', '')])) if im else ''
    own_text = alt_text + ' ' + fname.replace('-', ' ').replace('_', ' ')
    page_text = ' '.join([page_name, page.get('title', ''), ' '.join(page.get('h1', [])), page.get('og_title', '')])
    cov_own, cov_page = coverage(entity, own_text + ' ' + near), coverage(entity, page_text)
    cov_body = coverage(entity, page.get('text_head', ''))
    e_codes = codes(entity)
    has_year = any(is_year(x) for x in e_codes)
    conflict = []
    for label, found in (('ảnh', codes(alt_text) | codes(fname, numbers=False)), ('trang', codes(page_name))):
        found = {x for x in found if has_year or not is_year(x)}
        same_kind = {x[0].isalpha() for x in found} & {x[0].isalpha() for x in e_codes}
        if e_codes and found and same_kind and not (found & e_codes):
            conflict.append(f'{label} ghi mã {", ".join(sorted(found)[:3])} ≠ entity {", ".join(sorted(e_codes)[:3])}')
    if conflict:
        check = 'MÂU THUẪN: ' + '; '.join(conflict)
    elif im and cov_own >= 0.75:
        check = 'chú thích/alt của ảnh khớp tên entity'
    elif cov_page >= 0.75:
        check = 'tiêu đề/tên sản phẩm trang khớp entity' + ('' if im else ' (không tìm thấy ảnh trên trang)')
    elif cov_body >= 0.75:
        check = 'trang có nhắc entity trong bài, tiêu đề không khớp hẳn'
    else:
        check = 'trang KHÔNG nhắc rõ entity' + ('' if im else ', không tìm thấy ảnh trên trang')
    return {'status': 'ok', 'found': how or 'không', 'own': own, 'near': near, 'page': clip(page_name, 140),
            'check': check, 'conflict': bool(conflict), 'cov_own': round(cov_own, 2), 'cov_page': round(cov_page, 2)}
