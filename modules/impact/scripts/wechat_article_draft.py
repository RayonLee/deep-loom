"""Word 草稿导入使用的公众号文章编辑器与 WebBridge 辅助函数。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

DAEMON = "http://127.0.0.1:10086/command"
SESSION = "wechat-impact-article"
WECHAT_HOME = "https://mp.weixin.qq.com/"

# 直接连接本地 WebBridge，避免系统 HTTP 代理转发回环请求。
_WB_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wb(action, args=None, timeout=30):
    body = json.dumps({"action": action, "args": args or {}, "session": SESSION}).encode()
    req = urllib.request.Request(DAEMON, data=body, headers={"Content-Type": "application/json"})
    with _WB_OPENER.open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError("WebBridge 命令未完成。")
    return resp.get("data", {})


def ev(code, timeout=30):
    return wb("evaluate", {"code": code}, timeout=timeout).get("value")


def iter_tabs():
    bags = []
    try:
        bags.append(wb("list_tabs", timeout=15) or {})
    except Exception:
        pass
    try:
        bags.append(wb("list_browser_tabs", timeout=15) or {})
    except Exception:
        pass
    seen = set()
    for bag in bags:
        tabs = bag.get("tabs") if isinstance(bag, dict) else bag
        if not isinstance(tabs, list):
            continue
        for tab in tabs:
            tid = tab.get("tabId")
            url = tab.get("url") or ""
            key = (tid, url)
            if key in seen:
                continue
            seen.add(key)
            yield tab


def _maybe_start_daemon():
    exe = os.path.expanduser("~/.kimi-webbridge/bin/kimi-webbridge")
    if sys.platform.startswith("win"):
        exe += ".exe"
    if os.path.isfile(exe):
        try:
            subprocess.run([exe, "start"], timeout=30)
        except Exception:
            pass


def _launch_mp_home():
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", "Google Chrome", WECHAT_HOME], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run("cmd /c start chrome " + WECHAT_HOME, shell=True)
    else:
        subprocess.run(["xdg-open", WECHAT_HOME], check=False)


def ensure_extension():
    _maybe_start_daemon()
    try:
        wb("list_tabs", timeout=8)
        return True
    except Exception:
        pass
    _launch_mp_home()
    for i in range(6):
        time.sleep(20)
        try:
            wb("list_tabs", timeout=8)
            return True
        except Exception:
            print(f"  等待扩展连接... ({(i+1)*20}s)", flush=True)
    return False


def wechat_upload_version(src, temp_dir=None):
    """封面转约 1080px 宽 JPEG，产物放临时目录，不改原图。无 PIL 时退回原文件。"""
    try:
        from PIL import Image
    except ImportError:
        print("  警告：未安装 Pillow，封面按原文件上传", flush=True)
        return src
    with Image.open(src) as im:
        w, h = im.size
    low, ext = os.path.splitext(src)
    if ext.lower() in (".jpg", ".jpeg") and w <= 1080 and os.path.getsize(src) <= 1024 * 1024:
        return src
    out = os.path.join(temp_dir or tempfile.gettempdir(), f"wx_cover_{abs(hash(src)) & 0xffffffff}_{os.path.basename(low)}.jpg")
    with Image.open(src) as im:
        s = min(1.0, 1080 / max(w, 1))
        im2 = im.resize((int(w * s), int(h * s)), Image.LANCZOS).convert("RGB")
        im2.save(out, quality=88)
    return out


def looks_like_login() -> bool:
    try:
        st = ev(
            """(() => {
              const t = (document.body && document.body.innerText) || '';
              const hasToken = /token=\\d{5,}/.test(document.documentElement.innerHTML);
              const login = /扫码登录|请使用微信扫码|登录微信公众平台|loginpage/.test(t)
                            || !!document.querySelector('.login__type__container_scan, .qrcheck, img[src*="qrcode"]');
              return login && !hasToken;
            })()"""
        )
        return bool(st)
    except Exception:
        return False


def current_url() -> str:
    try:
        return ev("location.href") or ""
    except Exception:
        return ""


def attach_tab(tab_id) -> None:
    wb("attach_tab", {"tabId": tab_id}, timeout=20)
    try:
        wb("activate_tab", {"tabId": tab_id}, timeout=15)
    except Exception:
        pass


def dismiss_plugin_warning() -> bool:
    """只关闭「浏览器插件存在安全隐患」这一种对话框，按钮必须是「我知道了」。"""
    found = ev(
        """(() => {
          const dlgs = [...document.querySelectorAll('.weui-desktop-dialog__wrp')].filter(el => {
            const cs = getComputedStyle(el);
            const r = el.getBoundingClientRect();
            return cs.display !== 'none' && r.width > 0 && r.height > 0
                   && (el.innerText || '').includes('浏览器插件存在安全隐患');
          });
          if (!dlgs.length) return {ok:false, present:false};
          const btn = [...dlgs[0].querySelectorAll('button, a, .weui-desktop-btn')]
            .find(e => (e.textContent || '').trim() === '我知道了');
          if (!btn) return {ok:false, present:true, reason:'no-ok-btn'};
          document.querySelectorAll('[data-impact-dismiss]').forEach(e => e.removeAttribute('data-impact-dismiss'));
          btn.setAttribute('data-impact-dismiss', 'plugin-warning');
          return {ok:true, present:true};
        })()"""
    )
    if not (found and found.get("ok")):
        return False
    try:
        wb("mouse_click", {"selector": "[data-impact-dismiss='plugin-warning']"}, timeout=20)
    except Exception:
        ev("""(() => { const b=document.querySelector("[data-impact-dismiss='plugin-warning']"); if (b) b.click(); return !!b; })()""")
    time.sleep(0.8)
    return True


PROBE_JS = r"""(() => {
  const txt = (el) => (el && (el.textContent || el.value || '')).trim();
  const title = document.querySelector('textarea.js_title, textarea#title');
  const author = document.querySelector('input.js_author, input#author');
  const digest = document.querySelector('textarea#js_description, textarea.js_description, textarea[name=digest]');
  const body = document.querySelector('.view.rich_media_content .ProseMirror, .mock-iframe-body .ProseMirror');
  const save = document.querySelector('#js_submit');
  const cover = document.querySelector('#js_cover_area');
  return {
    title: !!title,
    author: !!author,
    digest: !!digest,
    body: !!body,
    save: !!save && /保存为草稿/.test((save.textContent || '')),
    cover: !!cover,
    hasType77Drop: /选择或拖拽图片/.test((document.body && document.body.innerText) || ''),
    createType0: /createType=0/.test(location.href),
    type77: /[?&]type=77(?:&|$)/.test(location.href)
  };
})()"""


def probe_editor():
    try:
        return ev(PROBE_JS, timeout=20) or {}
    except Exception:
        return {}


def wait_article_editor(timeout_s=45):
    """等待文章编辑器标题框出现。贴图页有「选择或拖拽图片」且无标题，不算就绪。"""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        time.sleep(3)
        if looks_like_login():
            raise RuntimeError("LOGIN_REQUIRED")
        last = probe_editor()
        if last.get("hasType77Drop") and not last.get("title"):
            continue
        if last.get("title") and (last.get("body") or last.get("createType0") or last.get("save")):
            return last
    return last


def fill_simple(selectors: list[str], value: str, kind: str) -> bool:
    if not value:
        return False
    js = (
        "(() => {\n"
        f"  const sels = {json.dumps(selectors)};\n"
        f"  const value = {json.dumps(value, ensure_ascii=False)};\n"
        "  for (const sel of sels) {\n"
        "    const el = document.querySelector(sel);\n"
        "    if (!el) continue;\n"
        "    el.focus();\n"
        "    el.value = value;\n"
        "    el.dispatchEvent(new Event('input', {bubbles:true}));\n"
        "    el.dispatchEvent(new Event('change', {bubbles:true}));\n"
        "    return sel;\n"
        "  }\n"
        "  return '';\n"
        "})()"
    )
    used = ev(js)
    if used:
        print(f"  已写入{kind}（{used}）", flush=True)
        return True
    print(f"  未找到{kind}控件，本步跳过", flush=True)
    return False


def fill_title(title: str) -> bool:
    ok = fill_simple(["textarea#title", "textarea.js_title"], title, "标题")
    pm = ev(
        """(() => {
          const el = document.querySelector('.title-editor__input .ProseMirror');
          if (!el) return {ok:false};
          const value = %s;
          el.focus();
          el.innerHTML = '';
          const p = document.createElement('p');
          p.textContent = value;
          el.appendChild(p);
          el.dispatchEvent(new Event('input', {bubbles:true}));
          el.dispatchEvent(new Event('change', {bubbles:true}));
          return {ok:true, text:(el.innerText||'').trim()};
        })()""" % json.dumps(title, ensure_ascii=False)
    )
    if pm and pm.get("ok"):
        print("  已写入可见标题（.title-editor__input .ProseMirror）", flush=True)
        ok = True
    return ok


def set_content(html_body: str, *, allow_short: bool = False):
    payload = json.dumps(html_body, ensure_ascii=False)
    code = f"""(() => {{
      const html = {payload};
      const el = document.querySelector('.view.rich_media_content .ProseMirror')
               || document.querySelector('.mock-iframe-body .ProseMirror')
               || document.querySelector('div.rich_media_content [contenteditable="true"]');
      if (!el) return {{ok:false, via:'none'}};
      if (el.closest('.title-editor__input') || el.closest('.original_primary_tips_input')) {{
        return {{ok:false, via:'refused-non-body'}};
      }}
      el.focus();
      try {{
        const sel = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(el);
        sel.removeAllRanges();
        sel.addRange(range);
        const okCmd = document.execCommand('insertHTML', false, html);
        const text = (el.innerText || '').replace(/\\s+/g,' ').trim();
        if (okCmd && (el.querySelectorAll('img').length > 0 || text.length > {0 if allow_short else 80})
            && text !== '从这里开始写正文') {{
          return {{ok:true, via:'execCommand', htmlLen:(el.innerHTML||'').length, nImg:el.querySelectorAll('img').length}};
        }}
      }} catch (e) {{}}
      el.innerHTML = html;
      el.dispatchEvent(new InputEvent('input', {{bubbles:true, cancelable:true, inputType:'insertFromPaste'}}));
      el.dispatchEvent(new Event('change', {{bubbles:true}}));
      const text = (el.innerText || '').replace(/\\s+/g,' ').trim();
      return {{
        ok: ({str(allow_short).lower()} ? !!(text || el.querySelector('img')) : text.length > 80)
            && text !== '从这里开始写正文',
        via:'innerHTML',
        htmlLen:(el.innerHTML||'').length,
        nImg:el.querySelectorAll('img').length
      }};
    }})()"""
    return ev(code, timeout=90) or {"ok": False, "via": "evaluate-failed"}


def cover_is_set() -> bool:
    st = ev(
        """(() => {
          const preview = document.querySelector('.js_cover_preview_new, .select-cover__preview');
          const cs = preview && getComputedStyle(preview);
          const bg = cs && (cs.backgroundImage || '');
          return !!(cs && cs.display !== 'none' && bg && bg !== 'none' && bg !== 'url("")' && /url\\(/i.test(bg));
        })()"""
    )
    return bool(st)


def open_cover_library() -> bool:
    """打开「选择图片」对话框。空封面走 #js_cover_null；已有封面则找同一入口或「修改」。"""
    opened = ev(
        """(() => {
          const click = (el) => { if (el) { el.click(); return true; } return false; };
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 8 && r.height > 8;
          };
          const a0 = document.querySelector('#js_cover_null a.js_imagedialog');
          if (click(a0)) return {ok:true, via:'cover-null'};
          const area = document.querySelector('#js_cover_area') || document;
          const a1 = area.querySelector('a.js_imagedialog, .js_imagedialog');
          if (click(a1)) return {ok:true, via:'imagedialog'};
          const labels = ['从图片库选择', '修改', '更换', '更换封面', '修改封面'];
          const nodes = [...area.querySelectorAll('a, button, span, div')];
          const hit = nodes.find(el => labels.includes((el.innerText || '').trim()) && vis(el));
          if (click(hit)) return {ok:true, via:'label'};
          return {ok:false, reason:'no-imagedialog'};
        })()"""
    )
    return bool(opened and opened.get("ok"))


def _dismiss_open_cover_dialogs():
    """关掉残留的「选择图片」或「编辑封面」，并等到对话框消失。"""
    ev(
        """(() => {
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && parseFloat(cs.opacity||'1') > 0;
          };
          let n = 0;
          for (const dlg of document.querySelectorAll('.weui-desktop-dialog')) {
            if (!vis(dlg)) continue;
            const t = dlg.innerText || '';
            if (!t.includes('选择图片') && !t.includes('编辑封面')) continue;
            const cancel = [...dlg.querySelectorAll('button, a')].find(el => ['取消', '关闭'].includes((el.innerText || '').trim()) && vis(el));
            if (cancel) { cancel.click(); n += 1; continue; }
            const close = dlg.querySelector('.weui-desktop-dialog__close, [aria-label="关闭"]');
            if (close && vis(close)) { close.click(); n += 1; }
          }
          return {ok: true, closed: n};
        })()"""
    )
    deadline = time.time() + 8
    while time.time() < deadline:
        time.sleep(0.4)
        st = ev(
            """(() => {
              const vis = (el) => {
                const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
                return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 20 && r.height > 20 && parseFloat(cs.opacity||'1') > 0;
              };
              const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && ((el.innerText||'').includes('选择图片') || ((el.querySelector('.weui-desktop-dialog__title')||{}).innerText||'').trim() === '编辑封面'));
              return {open: !!dlg};
            })()"""
        )
        if st and not st.get("open"):
            return


def _cover_picker_state():
    """读取「选择图片」对话框：图块、当前分组与张数。"""
    return ev(
        """(() => {
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && parseFloat(cs.opacity||'1') > 0;
          };
          const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && (el.innerText||'').includes('选择图片'));
          if (!dlg) return {ok:false, items:[], menus:[]};
          const items = [...dlg.querySelectorAll('.weui-desktop-img-picker__item')].map((el, i) => {
            const title = ((el.querySelector('.weui-desktop-img-picker__img-title')||{}).innerText||'').trim();
            const img = el.querySelector('img');
            const src = (img && (img.currentSrc || img.src)) || '';
            const thumb = el.querySelector('.weui-desktop-img-picker__img-thumb, .weui-desktop-img-picker__img') || img;
            const bg = thumb ? (getComputedStyle(thumb).backgroundImage || '') : '';
            const key = src || bg || title;
            return {i, title, key, selected: /selected/.test(el.className || '')};
          });
          const menus = [...dlg.querySelectorAll('.weui-desktop-menu__item')].map(el => {
            const text = (el.innerText || '').trim();
            const link = el.querySelector('.weui-desktop-menu__link') || el;
            const current = /link_current|_current|selected|active/.test(link.className || el.className || '');
            const m = text.match(/^(.*?)\\s*\\((\\d+)\\)\\s*$/);
            return {text, name: m ? m[1] : text, count: m ? parseInt(m[2], 10) : null, current};
          });
          const empty = (dlg.innerText || '').includes('暂无数据');
          return {ok:true, n: items.length, items, menus, empty};
        })()"""
    )


def _click_cover_menu(prefix: str) -> bool:
    """点击左侧分组，标题以 prefix 开头即可，如「未分组」「我的图片」。"""
    st = ev(
        """(() => {
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0 && parseFloat(cs.opacity||'1') > 0;
          };
          const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && (el.innerText||'').includes('选择图片'));
          if (!dlg) return {ok:false, reason:'no-dialog'};
          const want = %s;
          const item = [...dlg.querySelectorAll('.weui-desktop-menu__item')].find(el => vis(el) && (el.innerText||'').trim().startsWith(want));
          if (!item) return {ok:false, reason:'no-menu'};
          const link = item.querySelector('a.weui-desktop-menu__link') || item;
          link.click();
          return {ok:true, clicked: (item.innerText||'').trim()};
        })()"""
        % json.dumps(prefix, ensure_ascii=False)
    )
    return bool(st and st.get("ok"))


def _mark_cover_file_input() -> bool:
    st = ev(
        """(() => {
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 20 && r.height > 20 && parseFloat(cs.opacity||'1') > 0;
          };
          const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && (el.innerText||'').includes('选择图片'));
          if (!dlg) return {ok:false};
          const wrap = dlg.querySelector('.js_upload_btn_container, .webuploader-container') || dlg;
          const inputs = [...dlg.querySelectorAll('input[type=file]')];
          const pick = wrap.querySelector('input[type=file]') || inputs.find(e => (e.accept||'').includes('image/bmp')) || inputs[0];
          if (!pick) return {ok:false, reason:'no-file-input'};
          document.querySelectorAll('[data-impact-cover-file]').forEach(e => e.removeAttribute('data-impact-cover-file'));
          pick.setAttribute('data-impact-cover-file', '1');
          return {ok:true, accept: pick.accept || ''};
        })()"""
    )
    return bool(st and st.get("ok"))


def _menu_count(state, name: str):
    for m in (state or {}).get("menus") or []:
        if (m.get("name") or m.get("text") or "").startswith(name):
            return m.get("count")
    return None


def _unique_cover_upload_path(prepared: str) -> str:
    """每次上传用新文件名，并微调一个像素，避免微信对完全相同的图片去重后不出现新图块。"""
    directory, name = os.path.split(prepared)
    stem, ext = os.path.splitext(name)
    stamped = os.path.join(directory, f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}{ext or '.jpg'}")
    try:
        from PIL import Image
        im = Image.open(prepared).convert("RGB")
        px = im.load()
        w, h = im.size
        r, g, b = px[w - 1, h - 1]
        tick = int(time.time()) % 13 + 1
        px[w - 1, h - 1] = ((r + tick) % 256, g, b)
        im.save(stamped, "JPEG", quality=95, optimize=True)
    except Exception:
        if os.path.abspath(stamped) != os.path.abspath(prepared):
            shutil.copy2(prepared, stamped)
    return stamped



def _search_cover_library(keyword: str) -> bool:
    """在「选择图片」里按文件名搜索。搜索框可能是隐藏的，但仍可写入并提交。"""
    if not keyword:
        return False
    st = ev(
        """(() => {
          const vis = (el) => {
            const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
            return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 20 && r.height > 20;
          };
          const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && (el.innerText||'').includes('选择图片'));
          if (!dlg) return {ok:false};
          const input = [...dlg.querySelectorAll('input')].find(el => (el.placeholder||'').includes('查找图片'));
          if (!input) return {ok:false, reason:'no-search'};
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(input, %s);
          input.dispatchEvent(new Event('input', {bubbles:true}));
          input.dispatchEvent(new Event('change', {bubbles:true}));
          const btn = dlg.querySelector('button.weui-desktop-search__btn');
          if (btn) btn.click();
          return {ok:true, value: input.value, clicked: !!btn};
        })()"""
        % json.dumps(keyword, ensure_ascii=False)
    )
    return bool(st and st.get("ok"))


def upload_cover(path: str, *, temp_dir=None) -> bool:
    """从图片库选择 → 重新上传 → 到「未分组/我的图片」选中刚出现的图块 → 下一步 → 确认。

    微信常把刚上传的图片改名为 wx_数字_原名，相同像素还可能被去重；
    「最近使用」首页也不一定出现新图。因此上传前微调像素并换新文件名，
    上传后按分组张数变化和新图块来选中，不能只比对本地文件名。
    """
    upload_cover._searched_name = False
    prepared_source = wechat_upload_version(path, temp_dir=temp_dir)
    if temp_dir and prepared_source == path:
        prepared_source = shutil.copyfile(path, os.path.join(temp_dir, "cover.jpg"))
    prepared = _unique_cover_upload_path(prepared_source)
    filename = os.path.basename(prepared)
    dismiss_plugin_warning()
    _dismiss_open_cover_dialogs()
    if cover_is_set():
        print("  封面预览已存在，跳过上传", flush=True)
        return True

    if not open_cover_library():
        print("  未找到封面「从图片库选择」或「修改」入口", flush=True)
        return False

    deadline = time.time() + 20
    dialog_ready = False
    while time.time() < deadline:
        time.sleep(1)
        if _mark_cover_file_input():
            dialog_ready = True
            break
    if not dialog_ready:
        print("  「选择图片」对话框未出现，已停止封面上传", flush=True)
        return False

    if _click_cover_menu("未分组"):
        time.sleep(1.2)
    before = _cover_picker_state() or {}
    before_items = before.get("items") or []
    before_keys = [it.get("key") or "" for it in before_items]
    before_titles = [it.get("title") or "" for it in before_items]
    before_first = before_keys[0] if before_keys else ""
    before_first_title = before_titles[0] if before_titles else ""
    before_ungrouped = _menu_count(before, "未分组")
    before_mine = _menu_count(before, "我的图片")
    print(
        f"  上传前未分组={before_ungrouped} 我的图片={before_mine}，首页首张={before_first_title or '(空)'}，将提交 {filename}",
        flush=True,
    )

    if not _mark_cover_file_input():
        print("  未找到封面上传的 file input，已停止", flush=True)
        return False
    try:
        wb("upload", {"selector": "[data-impact-cover-file='1']", "files": [prepared]}, timeout=120)
    except Exception:
        print("  封面上传失败", flush=True)
        return False
    print(f"  已向图片库提交封面 {filename}", flush=True)

    selected = False
    last_titles = []
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(1.5)
        _click_cover_menu("未分组")
        now = _cover_picker_state() or {}
        if now.get("empty"):
            continue
        items = now.get("items") or []
        last_titles = [it.get("title") or "" for it in items]
        ungrouped = _menu_count(now, "未分组")
        mine = _menu_count(now, "我的图片")
        grown = (
            (ungrouped is not None and before_ungrouped is not None and ungrouped > before_ungrouped)
            or (mine is not None and before_mine is not None and mine > before_mine)
        )
        first_title = (items[0].get("title") or "") if items else ""
        print(
            f"  选图轮询：未分组={ungrouped} 我的图片={mine} 首张={first_title or '(空)'} 张数变化={grown}",
            flush=True,
        )
        hit_index = None
        stem = os.path.splitext(filename)[0]
        # Only trust a title match on the distinctive new filename, or a
        # genuinely new first-tile title. Do not treat refreshed thumbnail
        # URLs or a count increment alone as enough — that previously
        # selected the old first body photo as the cover.
        for it in items:
            title = (it.get("title") or "").strip()
            if title == filename or (stem and stem in title):
                hit_index = it.get("i")
                break
        if hit_index is None and items:
            if first_title and first_title != before_first_title:
                hit_index = items[0].get("i")
        if hit_index is None:
            if grown and not getattr(upload_cover, "_searched_name", False):
                upload_cover._searched_name = True
                if _search_cover_library(stem) or _search_cover_library(filename):
                    time.sleep(1.2)
                    now = _cover_picker_state() or {}
                    items = now.get("items") or []
                    last_titles = [it.get("title") or "" for it in items]
                    for it in items:
                        title = (it.get("title") or "").strip()
                        if title == filename or (stem and stem in title) or (title and title != before_first_title):
                            hit_index = it.get("i")
                            break
        if hit_index is None:
            continue
        st = ev(
            """(() => {
              const vis = (el) => {
                const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
                return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 0 && r.height > 0;
              };
              const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => vis(el) && (el.innerText||'').includes('选择图片'));
              if (!dlg) return {ok:false, reason:'dialog-gone'};
              const items = [...dlg.querySelectorAll('.weui-desktop-img-picker__item')];
              const hit = items[%d];
              if (!hit) return {ok:false, reason:'index-missing', n: items.length};
              if (!/selected/.test(hit.className || '')) {
                hit.click();
              }
              const next = [...dlg.querySelectorAll('button, a')].find(el => (el.innerText||'').trim() === '下一步' && vis(el));
              const disabled = !next || next.disabled || /disabled/.test(next.className || '');
              if (next && !disabled) {
                document.querySelectorAll('[data-impact-cover-next]').forEach(e => e.removeAttribute('data-impact-cover-next'));
                next.setAttribute('data-impact-cover-next', '1');
              }
              return {ok:true, selected: /selected/.test(hit.className || ''), nextEnabled: !disabled, title: ((hit.querySelector('.weui-desktop-img-picker__img-title')||{}).innerText||'').trim()};
            })()"""
            % int(hit_index)
        )
        if st and st.get("ok") and st.get("selected") and st.get("nextEnabled"):
            selected = True
            shown = st.get("title") or f"index={hit_index}"
            print(f"  已选中刚上传的封面（图库显示为 {shown}）", flush=True)
            break
    if not selected:
        shown = ", ".join(t or "(空标题)" for t in last_titles[:8]) or "(无图块)"
        print(f"  未在图片库中选中刚上传的 {filename}，当前可见标题：{shown}，已停止", flush=True)
        return False

    ev("""(() => { const b=document.querySelector('[data-impact-cover-next="1"]'); if (b) b.click(); return !!b; })()""")
    print("  已点击「下一步」", flush=True)

    confirmed = False
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(1)
        st = ev(
            """(() => {
              const vis = (el) => {
                const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
                return cs.display !== 'none' && cs.visibility !== 'hidden' && r.width > 20 && r.height > 20;
              };
              const dlg = [...document.querySelectorAll('.weui-desktop-dialog')].find(el => {
                const title = ((el.querySelector('.weui-desktop-dialog__title')||{}).innerText||'').trim();
                return vis(el) && title === '编辑封面';
              });
              if (!dlg) return {ok:false};
              const btn = [...dlg.querySelectorAll('button, a')].find(el => (el.innerText||'').trim() === '确认' && vis(el));
              if (!btn || btn.disabled || /disabled/.test(btn.className || '')) return {ok:false, reason:'no-confirm'};
              document.querySelectorAll('[data-impact-cover-confirm]').forEach(e => e.removeAttribute('data-impact-cover-confirm'));
              btn.setAttribute('data-impact-cover-confirm', '1');
              return {ok:true};
            })()"""
        )
        if st and st.get("ok"):
            ev("""(() => { const b=document.querySelector('[data-impact-cover-confirm="1"]'); if (b) b.click(); return !!b; })()""")
            confirmed = True
            break
    if not confirmed:
        print("  「编辑封面」未出现可点的「确认」，已停止", flush=True)
        return False
    print("  已在「编辑封面」点击「确认」", flush=True)

    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(1)
        if cover_is_set():
            print("  封面预览已写入", flush=True)
            return True
    print("  封面确认后未见预览，已停止", flush=True)
    return False


def click_save_draft(*, word_attempt: str, word_title: str, word_digest: str):
    if not word_attempt:
        return {"ok": False, "reason": "not-owned-new-page"}
    js = f"""(() => {{
      const label = (e) => (e.textContent || e.getAttribute('aria-label') || '').replace(/\\s+/g,' ').trim();
      const p = new URL(location.href).searchParams;
      if (location.origin !== 'https://mp.weixin.qq.com' || location.pathname !== '/cgi-bin/appmsg'
          || !['media/appmsg_edit', 'media/appmsg_edit_v2'].includes(p.get('t'))
          || p.get('isNew') !== '1' || p.get('type') !== '77' || p.get('createType') !== '0'
          || p.has('appmsgid') || p.has('share')
          || document.documentElement.dataset.impactWordAttempt !== {json.dumps(word_attempt)})
        return {{ok:false, reason:'not-owned-new-page'}};
      const title = document.querySelector('textarea#title, textarea.js_title')?.value?.trim();
      const shown = document.querySelector('.title-editor__input .ProseMirror')?.innerText?.trim();
      const digest = document.querySelector('textarea#js_description, textarea.js_description, textarea[name=digest]')?.value?.trim();
      if (title !== {json.dumps(word_title)} || shown !== title || digest !== {json.dumps(word_digest)})
        return {{ok:false, reason:'metadata-changed'}};
      const root = document.querySelector('#js_submit');
      const target = root?.querySelector('button') || root;
      if (!target || label(target) !== '保存为草稿') return {{ok:false, reason:'no-save-button'}};
      if (target.disabled || target.getAttribute('aria-disabled') === 'true'
          || !target.getClientRects().length || getComputedStyle(target).visibility !== 'visible')
        return {{ok:false, reason:'disabled-save-button'}};
      target.click();
      return {{ok:true}};
    }})()"""
    return ev(js, timeout=30)


def open_article_editor(home_tab, is_new_editor):
    """从官方后台首页点击「文章」，并等待符合条件的新标签。"""
    attach_tab(home_tab)
    time.sleep(1)
    if looks_like_login():
        raise RuntimeError("LOGIN_REQUIRED")
    dismiss_plugin_warning()

    marked = ev(
        """(() => {
          if (location.origin !== 'https://mp.weixin.qq.com' || location.pathname !== '/cgi-bin/home')
            return false;
          document.querySelectorAll('[data-impact-entry]').forEach(e => e.removeAttribute('data-impact-entry'));
          const items = [...document.querySelectorAll('.new-creation__menu-item')];
          const matches = items.filter(el => {
            const t = el.querySelector('.new-creation__menu-title');
            return t?.textContent?.trim() === '文章' && !!el.getClientRects().length
                && getComputedStyle(el).visibility === 'visible';
          });
          if (matches.length !== 1) return false;
          matches[0].setAttribute('data-impact-entry', 'article');
          return true;
        })()"""
    )
    if not marked:
        return None

    before = {tab.get("tabId") for tab in iter_tabs()}
    try:
        wb("mouse_click", {"selector": "[data-impact-entry='article']"}, timeout=20)
    except Exception:
        return None

    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(2)
        for tab in iter_tabs():
            tid, url = tab.get("tabId"), tab.get("url") or ""
            if tid is not None and tid not in before and is_new_editor(url):
                attach_tab(tid)
                return tid
    return None
