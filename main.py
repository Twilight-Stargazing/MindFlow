"""MindFlow - sơ đồ tư duy chạy offline (Python + pywebview).

Giao diện nằm hết trong ui/index.html. File này chỉ lo: cửa sổ (nhiều cửa sổ),
mở/lưu file .mmap, xuất OPML, lưu cài đặt, cảnh báo khi đóng lúc chưa lưu.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import webview

APP = 'MindFlow'
EXT = '.mmap'
FILTER = ('Sơ đồ MindFlow (*.mmap)', 'Tất cả (*.*)')
BASE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))  # _MEIPASS: khi đã đóng gói bằng PyInstaller
INDEX = str(BASE / 'ui' / 'index.html')
DATA = Path(os.environ.get('APPDATA') or Path.home() / '.config') / APP  # giữ nguyên khi gỡ/cài đè app
SETTINGS = DATA / 'settings.json'
DEFAULTS = {'theme': 'system', 'bg1': None, 'bg2': None, 'recent': [], 'auto_update': True, 'skip_version': None}
COLOR = re.compile(r'#[0-9a-fA-F]{6}')

_lock = threading.Lock()
APIS = []        # mỗi cửa sổ đang mở có 1 Api
RUNNING = False  # chỉ gọi evaluate_js khi vòng lặp giao diện đã chạy


# ---------- cài đặt, file, OPML (thuần Python, có test) ----------
def load_settings():
    s = dict(DEFAULTS, recent=[])
    try:
        s.update({k: v for k, v in json.loads(SETTINGS.read_text(encoding='utf-8')).items() if k in DEFAULTS})
    except (OSError, ValueError, AttributeError):
        pass
    return s


def save_settings(s):
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding='utf-8')


def read_doc(path):
    d = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(d, dict) or not isinstance(d.get('root'), dict):
        raise ValueError('không phải file sơ đồ MindFlow')
    return d


def write_doc(path, text):
    json.loads(text)  # chặn ghi dữ liệu hỏng
    p = Path(path)
    tmp = p.with_name(p.name + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, p)  # ghi tạm rồi đổi tên: lỗi giữa chừng không làm hỏng file cũ


def to_opml(doc):
    def walk(n, parent):
        o = ET.SubElement(parent, 'outline', text=str(n.get('t', '')))
        for c in n.get('c', []):
            walk(c, o)
    opml = ET.Element('opml', version='2.0')
    ET.SubElement(ET.SubElement(opml, 'head'), 'title').text = str(doc['root'].get('t', ''))
    walk(doc['root'], ET.SubElement(opml, 'body'))
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(opml, encoding='unicode')


def safe_name(s):
    return re.sub(r'[\\/:*?"<>|\r\n]+', ' ', s).strip()[:80] or 'So do'


# ---------- Windows: thanh tiêu đề tối, hộp thoại ----------
def system_dark():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize') as k:
            return winreg.QueryValueEx(k, 'AppsUseLightTheme')[0] == 0
    except Exception:
        return False


def is_dark(s):
    return s['theme'] == 'dark' or (s['theme'] == 'system' and system_dark())


def _bg():
    return '#202020' if is_dark(load_settings()) else '#F3F3F3'


def titlebar(w, dark):
    """Thanh tiêu đề tối cho khớp giao diện (Win 10 2004+ và Win 11). Lỗi thì bỏ qua."""
    try:
        import ctypes
        hwnd = w.native.Handle.ToInt64()
        v = ctypes.c_int(1 if dark else 0)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(v), 4)
    except Exception:
        pass


def _msgbox(text, yes_no=False):
    """Trả về True nếu bấm Yes. Không dùng API của pywebview nên không phụ thuộc phiên bản."""
    try:
        import ctypes
        flags = 0x40000 | (0x124 if yes_no else 0x30)  # topmost | Yes/No + icon hỏi + mặc định No | icon cảnh báo
        return ctypes.windll.user32.MessageBoxW(0, text, APP, flags) == 6
    except Exception:
        return True


def create_shortcut():
    """Tạo MindFlow.lnk ngoài màn hình nền, trỏ tới MindFlow.exe đang chạy (PowerShell có sẵn trong Windows)."""
    if not getattr(sys, 'frozen', False):
        return {'ok': False, 'error': 'Chỉ tạo được khi chạy MindFlow.exe (bản đã build/cài), không phải khi chạy bằng python.'}
    ps = ("$p=Join-Path ([Environment]::GetFolderPath('Desktop')) ($env:MF_NAME+'.lnk');"
          "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($p);"
          "$s.TargetPath=$env:MF_EXE;$s.WorkingDirectory=Split-Path $env:MF_EXE;"
          "$s.IconLocation=$env:MF_EXE+',0';$s.Save()")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                           env=dict(os.environ, MF_NAME=APP, MF_EXE=sys.executable),
                           capture_output=True, timeout=20, creationflags=0x08000000)  # 0x08000000: không bật cửa sổ đen
    except (OSError, subprocess.SubprocessError) as e:
        return {'ok': False, 'error': f'Không tạo được shortcut: {e}'}
    return {'ok': True} if r.returncode == 0 else {'ok': False, 'error': 'Không tạo được shortcut.'}


def _dlg(kind):
    fd = getattr(webview, 'FileDialog', None)
    return getattr(fd, kind) if fd else getattr(webview, kind + '_DIALOG')


def _broadcast(js, skip=None):
    if not RUNNING:
        return
    for w in list(webview.windows):
        if w is not skip:
            try:
                w.evaluate_js(js)
            except Exception:
                pass


def _remember(path):
    s = load_settings()
    p = str(Path(path))
    s['recent'] = [p] + [r for r in s['recent'] if os.path.normcase(r) != os.path.normcase(p)][:9]
    save_settings(s)
    _broadcast('window.onRecent&&onRecent()')


# ---------- tự cập nhật qua GitHub Releases ----------
GITHUB_REPO = 'Twilight-Stargazing/MindFlow'  # ĐỔI thành 'tài-khoản/tên-repo' của bạn; repo phải để Public
UA = f'{APP}-updater'
PENDING = None  # thông tin bản mới nhất vừa dò được


def _read_version():
    """Đọc version.txt (cạnh main.py; trong exe thì build phải có --add-data "version.txt;.")."""
    try:
        return (BASE / 'version.txt').read_text(encoding='utf-8-sig').strip().lstrip('vV') or None
    except OSError:
        return None


VERSION = _read_version()  # None = không đọc được -> tắt dò cập nhật, không báo nhầm


def _ver(s):
    """'v1.2' -> (1, 2, 0). Không hiểu được thì trả None."""
    m = re.match(r'\s*[vV]?(\d+(?:\.\d+){0,2})', str(s))
    return tuple(int(x) for x in (m.group(1).split('.') + ['0', '0'])[:3]) if m else None


def check_update():
    """Hỏi GitHub bản mới nhất. Trả dict nếu có bản MỚI HƠN, None nếu đang là bản mới nhất.
    Lỗi mạng/GitHub thì ném ngoại lệ (người gọi tự quyết định im lặng hay báo)."""
    cur = _ver(VERSION)
    if not cur:
        raise RuntimeError('không đọc được số phiên bản của app')
    req = urllib.request.Request(f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest',
                                 headers={'User-Agent': UA, 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req, timeout=8) as r:
        rel = json.load(r)
    new = _ver(rel.get('tag_name', ''))
    if not new or new <= cur:
        return None
    exes = [a for a in (rel.get('assets') or []) if str(a.get('name', '')).lower().endswith('.exe')]
    exes.sort(key=lambda a: 'setup' not in a['name'].lower())  # ưu tiên file có chữ "Setup"
    if not exes:  # release chưa kịp đính kèm bộ cài
        return None
    a = exes[0]
    return {'version': '.'.join(map(str, new)), 'major': new[0] > cur[0],
            'notes': (rel.get('body') or '').strip(), 'name': a['name'],
            'url': a.get('browser_download_url', ''), 'size': a.get('size') or 0}


def install_update(info):
    """Tải bộ cài rồi chạy nó (tự cài đè, mở lại app) và đóng app này. Lỗi thì trả {'ok': False, 'error': ...}."""
    if not info:
        return {'ok': False, 'error': 'Chưa có thông tin bản cập nhật.'}
    if any(a._dirty for a in APIS):
        return {'ok': False, 'error': 'Hãy lưu các sơ đồ đang mở rồi cập nhật lại.'}
    if not info['url'].lower().startswith(f'https://github.com/{GITHUB_REPO}/releases/download/'.lower()):
        return {'ok': False, 'error': 'Địa chỉ tải không hợp lệ.'}
    folder = DATA / 'update'
    dest = folder / Path(info['name']).name
    try:
        shutil.rmtree(folder, ignore_errors=True)  # dọn bộ cài của lần trước
        folder.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(info['url'], headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, 'wb') as f:
            shutil.copyfileobj(r, f)
        if info['size'] and dest.stat().st_size != info['size']:
            raise OSError('file tải về bị thiếu, hãy thử lại')
        subprocess.Popen([str(dest), '/SILENT', '/CLOSEAPPLICATIONS', '/RELAUNCH=1'])
    except Exception as e:
        return {'ok': False, 'error': f'Không cập nhật được: {e}'}
    for w in list(webview.windows):  # đóng app để bộ cài ghi đè được MindFlow.exe
        try:
            w.destroy()
        except Exception:
            pass
    return {'ok': True}


def _skip(version):
    s = load_settings()
    s['skip_version'] = str(version)
    save_settings(s)


TOAST_JS = r"""
(function (d) {
  var old = document.getElementById('mf-update');
  if (old) old.remove();
  var box = document.createElement('div');
  box.id = 'mf-update';
  box.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;width:300px;padding:14px 16px;' +
    'border-radius:8px;font:14px/1.4 "Segoe UI",sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.35);border:1px solid ' +
    (d.dark ? '#444;background:#2b2b2b;color:#f3f3f3' : '#d0d0d0;background:#fff;color:#202020');
  var msg = document.createElement('div');
  msg.textContent = 'Có MindFlow ' + d.version + ' mới.';
  msg.style.marginBottom = '10px';
  var row = document.createElement('div');
  row.style.cssText = 'display:flex;gap:8px;justify-content:flex-end';
  function btn(label, primary, fn) {
    var b = document.createElement('button');
    b.textContent = label;
    b.style.cssText = 'padding:5px 14px;border-radius:5px;font:inherit;cursor:pointer;border:1px solid ' +
      (primary ? '#0067c0;background:#0067c0;color:#fff' : (d.dark ? '#555;background:transparent;color:#f3f3f3' : '#bbb;background:transparent;color:#202020'));
    b.onclick = fn;
    row.appendChild(b);
    return b;
  }
  var go = btn('Cập nhật ngay', true, function () {
    go.disabled = later.disabled = true;
    msg.textContent = 'Đang tải bản cập nhật…';
    window.pywebview.api.start_update().then(function (r) {
      if (r && !r.ok) { msg.textContent = r.error; go.disabled = later.disabled = false; }
    });
  });
  var later = btn('Để sau', false, function () {
    window.pywebview.api.dismiss_update(d.version);
    box.remove();
  });
  box.appendChild(msg);
  box.appendChild(row);
  document.body.appendChild(box);
})(__DATA__);
"""


def _update_worker():
    """Chạy nền 1 lần khi mở app: hỏi GitHub, có bản mới thì báo.
    Bản lớn (số đầu tăng: 2.0, 3.0...): luôn hiện cửa sổ hỏi, kể cả khi tắt tự dò.
    Bản nhỏ (1.2, 1.3...): chỉ báo ở góc màn hình khi 'auto_update' đang bật."""
    global PENDING
    time.sleep(4)  # chờ cửa sổ dựng xong
    try:
        info = check_update()
    except Exception:
        return  # không có mạng / GitHub lỗi: im lặng
    s = load_settings()
    if not info or info['version'] == s['skip_version']:
        return
    PENDING = info
    if info['major']:
        notes = info['notes'][:600]
        text = (f"Đã có MindFlow {info['version']} (bạn đang dùng {VERSION}).\n\n"
                + (notes + '\n\n' if notes else '') + 'Cập nhật ngay bây giờ?')
        if _msgbox(text, yes_no=True):
            r = install_update(info)
            if not r['ok']:
                _msgbox(r['error'])
        else:
            _skip(info['version'])
    elif s['auto_update']:
        ws = list(webview.windows)
        if ws:
            try:
                ws[0].evaluate_js(TOAST_JS.replace('__DATA__', json.dumps(
                    {'version': info['version'], 'dark': is_dark(s)})))
            except Exception:
                pass


# ---------- cửa sổ ----------
class Api:
    """Mỗi cửa sổ có 1 Api riêng. Thuộc tính bắt đầu bằng _ để pywebview không đưa sang JS."""

    def __init__(self, role, doc=None, path=None):
        self._role, self._doc, self._path = role, doc, path
        self._dirty = False
        self._w = None

    def _closing(self):
        dirty = any(a._dirty for a in APIS) if self._role == 'home' else self._dirty
        if dirty and not _msgbox('Có thay đổi chưa lưu sẽ bị mất. Vẫn đóng?', yes_no=True):
            return False

    # --- JS gọi vào ---
    def init(self):
        return {'role': self._role, 'doc': self._doc, 'path': self._path, 'settings': load_settings(),
                'version': VERSION}

    def set_state(self, dirty, name):
        self._dirty = bool(dirty)
        if self._w and self._role == 'map':
            self._w.set_title(f"{name or 'Sơ đồ mới'}{'*' if dirty else ''} - {APP}")

    def new_map(self, doc):
        _open_map(doc)

    def open_file(self, path=None):
        if not path:
            r = self._w.create_file_dialog(_dlg('OPEN'), file_types=FILTER)
            if not r:
                return {'ok': False}
            path = r[0]
        return _open_path(path)

    def save(self, text, save_as=False):
        try:
            name = json.loads(text)['root'].get('t', '')
            path = self._path
            if save_as or not path:
                r = self._w.create_file_dialog(_dlg('SAVE'), save_filename=safe_name(name) + EXT, file_types=FILTER)
                if not r:
                    return {'ok': False}
                path = r if isinstance(r, str) else r[0]
                if not path.lower().endswith(EXT):
                    path += EXT
            write_doc(path, text)
        except (OSError, ValueError, KeyError) as e:
            return {'ok': False, 'error': f'Không lưu được: {e}'}
        self._path = path
        _remember(path)
        return {'ok': True, 'path': path}

    def export_opml(self, text):
        try:
            doc = json.loads(text)
            r = self._w.create_file_dialog(_dlg('SAVE'), save_filename=safe_name(doc['root'].get('t', '')) + '.opml',
                                           file_types=('OPML (*.opml)', 'Tất cả (*.*)'))
            if not r:
                return {'ok': False}
            path = r if isinstance(r, str) else r[0]
            Path(path).write_text(to_opml(doc), encoding='utf-8')
        except (OSError, ValueError, KeyError) as e:
            return {'ok': False, 'error': f'Không xuất được: {e}'}
        return {'ok': True, 'path': path}

    def set_settings(self, s, dark=False):
        cur = load_settings()
        if s.get('theme') in ('system', 'light', 'dark'):
            cur['theme'] = s['theme']
        for k in ('bg1', 'bg2'):
            v = s.get(k)
            cur[k] = v if isinstance(v, str) and COLOR.fullmatch(v) else None
        if isinstance(s.get('auto_update'), bool):
            cur['auto_update'] = s['auto_update']
        save_settings(cur)
        for w in list(webview.windows):
            titlebar(w, bool(dark))
        out = {k: cur[k] for k in ('theme', 'bg1', 'bg2', 'auto_update')}
        _broadcast(f'applySettings({json.dumps(out)})', skip=self._w)

    def check_update_now(self):
        """Nút 'Kiểm tra cập nhật' trong Cài đặt (nếu có) gọi hàm này."""
        global PENDING
        try:
            info = check_update()
        except Exception as e:
            return {'ok': False, 'error': f'Không kiểm tra được cập nhật: {e}'}
        PENDING = info
        return {'ok': True, 'current': VERSION, 'available': bool(info),
                'version': info['version'] if info else None, 'notes': info['notes'] if info else ''}

    def start_update(self):
        return install_update(PENDING)

    def dismiss_update(self, version):
        _skip(version)

    def create_shortcut(self):
        return create_shortcut()

    def get_recent(self):
        out = []
        for p in load_settings()['recent']:
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            out.append({'path': p, 'name': Path(p).stem, 'dir': str(Path(p).parent), 'mtime': m})
        return out


def _make_window(api, w, h, min_size):
    win = webview.create_window(APP, INDEX, js_api=api, width=w, height=h, min_size=min_size, background_color=_bg())
    api._w = win
    APIS.append(api)
    win.events.closing += api._closing
    win.events.closed += lambda: APIS.remove(api) if api in APIS else None
    win.events.shown += lambda: titlebar(win, is_dark(load_settings()))
    return win


def _open_map(doc, path=None):
    _make_window(Api('map', doc, path), 1180, 760, (720, 460))


def _open_path(path):
    key = os.path.normcase(os.path.abspath(path))
    for a in APIS:  # file đã mở rồi thì đưa cửa sổ đó lên, không mở lần hai
        if a._path and os.path.normcase(os.path.abspath(a._path)) == key:
            try:
                a._w.restore()
            except Exception:
                pass
            return {'ok': True}
    try:
        doc = read_doc(path)
    except (OSError, ValueError) as e:
        return {'ok': False, 'error': f'Không mở được file: {e}'}
    _open_map(doc, path)
    _remember(path)
    return {'ok': True}


def _ready():
    global RUNNING
    RUNNING = True
    threading.Thread(target=_update_worker, daemon=True).start()


def main():
    files = [a for a in sys.argv[1:] if a.lower().endswith(EXT) and os.path.isfile(a)]  # mở đúp file .mmap
    for f in files:
        r = _open_path(f)
        if not r['ok']:
            _msgbox(r['error'])
    if not APIS:  # không có file hợp lệ nào thì mở cửa sổ chính
        _make_window(Api('home'), 1040, 700, (780, 500))
    try:
        webview.start(_ready, gui='edgechromium')
    except Exception as e:
        _msgbox('Không khởi động được cửa sổ.\n\nNếu máy chưa có WebView2 Runtime, cài từ:\n'
                f'https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n{e}')


if __name__ == '__main__':
    main()
