#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一篇公众号图文写入微信草稿箱（Chrome + Kimi WebBridge），停在发送前。

用法:
  python3 scripts/wechat_article_draft.py \
    --title "标题" --digest "摘要" --html article.wechat.html \
    [--cover cover.jpg] [--author "署名"] [--session wechat-impact-article]
    [--appmsgid 100000000]

与日报 modules/daily-scan/scripts/wechat_draft.py 同源：同一套桥接、Chrome 启动、
首页取 token、保存后回读、落 _uploaded_<标题>.flag。区别在于打开的是「文章」编辑器
（type=77&createType=0），而不是日报图片帖（type=77&createType=8）。不要改日报
那份脚本，也不要复用其会话名。

选择器状态（2026-11-18，已在本机对当前公众号后台实机锁定）：
  入口：已登录首页「新的创作」→ 对标题为「文章」的菜单项做可信 mouse_click
       （CSS：.new-creation__menu-item，其子节点 .new-creation__menu-title 精确为「文章」）。
       等价 URL：/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit&isNew=1&type=77&createType=0
       不要再用 type=10，也不要进入贴图（createType=8）。
  标题：textarea#title / textarea.js_title，同时写入可见的 .title-editor__input .ProseMirror
  作者：input#author / input.js_author
  摘要：textarea#js_description
  正文：仅 .view.rich_media_content .ProseMirror（或 .mock-iframe-body .ProseMirror）；
       用选中全部 + document.execCommand('insertHTML')。禁止写入标题 ProseMirror 或编者荐语。
  封面：#js_cover_area 静止时没有 file input。合成点击 #js_cover_null a.js_imagedialog
       打开「选择图片」，向对话框内 accept 含 image/bmp 的 file input 重新上传；
       上传前记下已有图块，上传后选中新增的那一张（微信常把文件显示为 wx_数字_原名，
       标题也可能为空，故不能只比对本地文件名），点「下一步」，在「编辑封面」点「确认」。
       成功标志：.js_cover_preview_new 显示且 background-image 有 mmbiz 地址。
  保存：#js_submit 内按钮，文案精确为「保存为草稿」。禁止点击发表 / 群发 / 发送。
  若出现「浏览器插件存在安全隐患」对话框，只点该框内的「我知道了」。

退出码: 0 成功 / 1 参数或本地文件 / 2 扩展不可用 / 3 登录或编辑器未就绪
        / 4 封面上传失败（若要求必须有封面才会到这一支） / 5 保存失败
        / 6 回读验证失败 / 8 控件无法定位（已停在编辑页）
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

DAEMON = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "wechat-impact-article"
HOME = "https://mp.weixin.qq.com/"
# 官方「文章」入口（首页 creationMenuItems id=0 / goAppmsg(0)）。
# 贴图是 type=77&createType=8；不要与之混淆。type=10 不是本账号「文章」入口。
EDITOR = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit"
    "&isNew=1&type=77&createType=0&token={token}&lang=zh_CN"
)
REOPEN = (
    "https://mp.weixin.qq.com/cgi-bin/appmsg?t=media/appmsg_edit_v2&action=edit"
    "&type=77&appmsgid={appmsgid}&token={token}&lang=zh_CN"
)

PUBLISH_LABELS = ("发表", "群发", "发布", "定时群发", "发送")
SAVE_LABELS = ("保存为草稿", "保存草稿")

SESSION = DEFAULT_SESSION

# 本机守护进程必须直连。macOS 的系统代理会出现在 urllib.getproxies() 里，
# 即使环境变量没有 http_proxy；若把 127.0.0.1:10086 送进本地代理，请求会超时，
# 脚本会误报「扩展始终未连接」。不要改日报脚本。
_WB_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wb(action, args=None, timeout=30):
    body = json.dumps({"action": action, "args": args or {}, "session": SESSION}).encode()
    req = urllib.request.Request(DAEMON, data=body, headers={"Content-Type": "application/json"})
    with _WB_OPENER.open(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode())
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", {}).get("message", "unknown")[:200])
    return resp.get("data", {})


def ev(code, timeout=30):
    return wb("evaluate", {"code": code}, timeout=timeout).get("value")


def navigate_safe(url, timeout=90, tab_id=None):
    """打开页面。微信后台偶发超过桥接默认 30s 仍未 complete，此时改为检查已打开的 URL。"""
    args = {"url": url}
    if tab_id:
        args["tabId"] = tab_id
    try:
        return wb("navigate", args, timeout=timeout)
    except Exception as e:
        print(f"  警告：navigate 未在时限内完成（{e}），改为检查当前页", flush=True)
        return None


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


def token_from_open_tabs():
    """优先从已登录的公众号页取 token，避免再打开会超时的 mp 根路径。"""
    fallback = (None, None)
    for tab in iter_tabs():
        url = tab.get("url") or ""
        if "mp.weixin.qq.com" not in url:
            continue
        m = re.search(r"token=(\d{5,})", url)
        if not m:
            continue
        if "cgi-bin/home" in url:
            return m.group(1), tab.get("tabId")
        if fallback[0] is None:
            fallback = (m.group(1), tab.get("tabId"))
    return fallback


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
        subprocess.run(["open", "-a", "Google Chrome", HOME], check=False)
    elif sys.platform.startswith("win"):
        subprocess.run("cmd /c start chrome " + HOME, shell=True)
    else:
        subprocess.run(["xdg-open", HOME], check=False)


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


def extract_body(html_text: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html_text, re.S | re.I)
    return (m.group(1).strip() if m else html_text).strip()


def looks_like_login() -> bool:
    try:
        st = ev(
            """(() => {
              const t = (document.body && document.body.innerText) || '';
              const hasToken = /token=\\d{5,}/.test(document.documentElement.innerHTML);
              const login = /扫码登录|请使用微信扫码|登录微信公众平台|loginpage/.test(t)
                            || !!document.querySelector('.login__type__container_scan, .qrcheck, img[src*="qrcode"]');
              return {login, hasToken, snippet: t.slice(0, 80)};
            })()"""
        )
        return bool(st and st.get("login") and not st.get("hasToken"))
    except Exception:
        return False


def current_url() -> str:
    try:
        return ev("location.href") or ""
    except Exception:
        return ""


def is_article_editor_url(url: str, allow_saved: bool = False) -> bool:
    if "mp.weixin.qq.com" not in (url or "") or "appmsg" not in url:
        return False
    if "createType=8" in url or "share=1" in url:
        return False
    if "createType=0" in url and "type=77" in url:
        return True
    if allow_saved and "type=77" in url and "appmsgid=" in url and "createType=8" not in url:
        return True
    return False


def find_new_article_tab():
    """复用已打开、尚未保存的「文章」编辑页，避免再开一篇。"""
    for tab in iter_tabs():
        url = tab.get("url") or ""
        if is_article_editor_url(url, allow_saved=False) and "appmsgid=" not in url:
            return tab.get("tabId"), url
    return None, None


def find_saved_article_tab(appmsgid: str):
    """复用已打开的指定草稿编辑页。"""
    want = f"appmsgid={appmsgid}"
    for tab in iter_tabs():
        url = tab.get("url") or ""
        if is_article_editor_url(url, allow_saved=True) and want in url:
            return tab.get("tabId"), url
    return None, None


def open_existing_draft(token: str, appmsgid: str):
    """打开已有草稿。优先复用已打开的标签；否则用 REOPEN 地址。打不开就返回失败，由调用方决定是否新建。"""
    exist_id, exist_url = find_saved_article_tab(appmsgid)
    if exist_id:
        print(f"  复用已打开的草稿 tab={exist_id} appmsgid={appmsgid}", flush=True)
        attach_tab(exist_id)
        return exist_id, exist_url, "reopen-existing-tab"
    reopen = REOPEN.format(appmsgid=appmsgid, token=token)
    print(f"  打开已有草稿 appmsgid={appmsgid}", flush=True)
    navigate_safe(reopen, timeout=90)
    deadline = time.time() + 40
    while time.time() < deadline:
        time.sleep(2)
        tid, url = find_saved_article_tab(appmsgid)
        if tid:
            attach_tab(tid)
            return tid, url, "reopen-appmsgid"
        url = current_url() or ""
        if is_article_editor_url(url, allow_saved=True) and f"appmsgid={appmsgid}" in url:
            return None, url, "reopen-appmsgid"
        if looks_like_login():
            raise RuntimeError("LOGIN_REQUIRED")
    print(f"DRAFT_FAIL 未能打开已有草稿 appmsgid={appmsgid}。当前页: {current_url()}。将尝试另存新草稿。", flush=True)
    return None, current_url(), "reopen-timeout"


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
    url: location.href,
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
    except Exception as e:
        return {"error": str(e)[:120], "url": current_url()}


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


def upload_cover(path: str, replace: bool = False, *, temp_dir=None) -> bool:
    """从图片库选择 → 重新上传 → 到「未分组/我的图片」选中刚出现的图块 → 下一步 → 确认。

    replace=True 时，已有封面也要打开图片库换成新图，不能因为旧预览还在就跳过。
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
    if cover_is_set() and not replace:
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
    except Exception as e:
        print(f"  封面上传失败: {e}", flush=True)
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

def click_save_draft(*, word_attempt: str = "", word_title: str = "", word_digest: str = ""):
    js = f"""(() => {{
      const publish = {json.dumps(PUBLISH_LABELS, ensure_ascii=False)};
      const preferred = {json.dumps(SAVE_LABELS, ensure_ascii=False)};
      const label = (e) => (e.textContent || e.getAttribute('aria-label') || '').replace(/\\s+/g,' ').trim();
      const isPublish = (e) => publish.some(p => label(e) === p || (p !== '发送' && label(e).includes(p) && !label(e).includes('保存')));
      const root = document.querySelector('#js_submit');
      let target = null;
      if (root) {{
        const inner = root.matches('button') ? root : (root.querySelector('button') || root);
        if (label(inner) === '保存为草稿') target = inner;
      }}
      const attempt = {json.dumps(word_attempt)};
      if (attempt) {{
        const p = new URL(location.href).searchParams;
        if (location.origin !== 'https://mp.weixin.qq.com' || location.pathname !== '/cgi-bin/appmsg'
            || p.get('isNew') !== '1' || p.get('type') !== '77' || p.get('createType') !== '0'
            || p.has('appmsgid') || p.has('share')
            || document.documentElement.dataset.impactWordAttempt !== attempt)
          return {{ok:false, reason:'not-owned-new-page'}};
        const title = document.querySelector('textarea#title, textarea.js_title')?.value?.trim();
        const shown = document.querySelector('.title-editor__input .ProseMirror')?.innerText?.trim();
        const digest = document.querySelector('textarea#js_description, textarea.js_description, textarea[name=digest]')?.value?.trim();
        if (title !== {json.dumps(word_title)} || shown !== title || digest !== {json.dumps(word_digest)})
          return {{ok:false, reason:'metadata-changed'}};
      }}
      if (!target && !attempt) {{
        const all = [...document.querySelectorAll('button, a, [role=button], .btn')];
        target = all.find(e => preferred.includes(label(e)));
      }}
      if (!target) return {{ok:false, reason:'no-save-button'}};
      if (attempt && (target.disabled || target.getAttribute('aria-disabled') === 'true'
          || !target.getClientRects().length)) return {{ok:false, reason:'disabled-save-button'}};
      if (isPublish(target) || !preferred.includes(label(target))) {{
        return {{ok:false, reason:'refused-publish', label: label(target)}};
      }}
      target.click();
      return {{ok:true, label: label(target), id: target.id || (target.parentElement && target.parentElement.id) || ''}};
    }})()"""
    return ev(js, timeout=30)


def read_appmsgid():
    return ev(
        """(() => {
          const s = (document.body && document.body.innerText) || '';
          const m = location.href.match(/appmsgid=(\\d+)/);
          return {saved: /手动保存|已保存|保存成功/.test(s), id: m ? m[1] : null, href: location.href};
        })()"""
    ) or {}


def read_title():
    return ev(
        """(() => {
          const a = (document.querySelector('textarea#title, textarea.js_title')||{}).value || '';
          const b = ((document.querySelector('.title-editor__input .ProseMirror')||{}).innerText||'').trim();
          return a || b || '';
        })()"""
    ) or ""


def open_article_editor(token: str, home_tab=None, *, reuse: bool = True):
    """优先复用已打开的新建文章页；否则从首页用可信点击打开「文章」。"""
    exist_id, exist_url = find_new_article_tab() if reuse else (None, None)
    if exist_id:
        print(f"  复用已打开的文章编辑页 tab={exist_id}", flush=True)
        attach_tab(exist_id)
        return exist_id, exist_url, "reuse-open-tab"

    if not home_tab:
        print("DRAFT_FAIL 没有已登录的公众号首页标签，无法点击「文章」入口。已停止。")
        return None, current_url(), "no-home-tab"

    print("  从首页点击「文章」入口", flush=True)
    attach_tab(home_tab)
    time.sleep(1)
    if looks_like_login():
        raise RuntimeError("LOGIN_REQUIRED")
    dismiss_plugin_warning()

    marked = ev(
        """(() => {
          document.querySelectorAll('[data-impact-entry]').forEach(e => e.removeAttribute('data-impact-entry'));
          const items = [...document.querySelectorAll('.new-creation__menu-item')];
          const hit = items.find(el => {
            const t = el.querySelector('.new-creation__menu-title');
            return t && (t.textContent || '').trim() === '文章';
          });
          if (!hit) return {ok:false, n: items.length, titles: items.map(el => ((el.querySelector('.new-creation__menu-title')||{}).textContent||'').trim())};
          hit.setAttribute('data-impact-entry', 'article');
          return {ok:true, text: (hit.innerText||'').replace(/\\s+/g,' ').trim()};
        })()"""
    )
    if not (marked and marked.get("ok")):
        print(f"DRAFT_FAIL 首页未见「文章」入口。当前页: {current_url()} 探测: {json.dumps(marked, ensure_ascii=False)}")
        return None, current_url(), "no-article-entry"

    before = {tab.get("tabId") for tab in iter_tabs()}
    try:
        wb("mouse_click", {"selector": "[data-impact-entry='article']"}, timeout=20)
    except Exception as e:
        print(f"DRAFT_FAIL 点击「文章」未成功（{e}）。当前页: {current_url()}。已停止，未改点其他入口。")
        return None, current_url(), "article-click-failed"

    deadline = time.time() + 25
    while time.time() < deadline:
        time.sleep(2)
        for tab in iter_tabs():
            tid, url = tab.get("tabId"), tab.get("url") or ""
            if tid in before:
                continue
            if is_article_editor_url(url):
                attach_tab(tid)
                return tid, url, "home-article-click"
        # 有时新标签已被记入 before 之后才改 URL
        if reuse:
            tid, url = find_new_article_tab()
            if tid:
                attach_tab(tid)
                return tid, url, "home-article-click"

    print(f"DRAFT_FAIL 点击「文章」后未在时限内出现 type=77&createType=0 编辑页。当前页: {current_url()}。已停止。")
    return None, current_url(), "article-tab-timeout"


def Path_read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main() -> int:
    global SESSION
    ap = argparse.ArgumentParser(description="把图文写入微信公众号草稿箱，停在发送前")
    ap.add_argument("--title", required=True, help="草稿标题")
    ap.add_argument("--digest", default="", help="摘要 / 分享描述")
    ap.add_argument("--html", required=True, help="本地预览 HTML（可含完整页面，脚本会取 body）")
    ap.add_argument("--cover", default="", help="封面图路径。可交稿必须提供；暂缺时仍可尝试写入正文，但本篇不算可交稿")
    ap.add_argument("--author", default="", help="作者署名（可选）")
    ap.add_argument("--session", default=DEFAULT_SESSION, help="WebBridge 会话名，不要用 wechat-daily-publish")
    ap.add_argument("--appmsgid", default="", help="若提供，则优先打开并更新已有草稿；打不开再另存新草稿")
    ap.add_argument("--dry-run", action="store_true", help="只检查本地文件，不启动浏览器")
    args = ap.parse_args()

    if args.session == "wechat-daily-publish":
        print("DRAFT_FAIL 拒绝使用日报会话名 wechat-daily-publish，请用 wechat-impact-article")
        return 1
    SESSION = args.session

    title = args.title.strip()
    digest = args.digest.strip()
    if len(title) > 64:
        print(f"  警告：标题 {len(title)} 字，可能被后台截断", flush=True)
    if len(digest) > 120:
        print(f"  警告：摘要 {len(digest)} 字，可能被后台截断", flush=True)
    html_path = os.path.abspath(args.html)
    if not os.path.isfile(html_path):
        print(f"DRAFT_FAIL 找不到 HTML: {html_path}")
        return 1
    html_body = extract_body(Path_read(html_path))
    if len(re.sub(r"<[^>]+>", "", html_body).strip()) < 20:
        print("DRAFT_FAIL HTML 正文过短")
        return 1
    cover = os.path.abspath(args.cover) if args.cover else ""
    if cover and not os.path.isfile(cover):
        print(f"DRAFT_FAIL 找不到封面: {cover}")
        return 1
    if args.dry_run:
        print("DRAFT_OK dry-run 本地文件检查通过（未启动浏览器）")
        return 0

    if not ensure_extension():
        print("DRAFT_FAIL 扩展始终未连接（Chrome 未开或扩展被禁用）")
        return 2

    token, home_tab = token_from_open_tabs()
    if not token:
        navigate_safe(HOME, timeout=90)
        time.sleep(3)
        if looks_like_login():
            print("DRAFT_FAIL 首页需要扫码登录。已把窗口留在登录页，请扫码后重试。")
            return 3
        token, home_tab = token_from_open_tabs()
    if not token:
        print("DRAFT_FAIL 首页未取到 token（可能登录过期，请扫码重登后重试）")
        return 3

    requested_appmsgid = (args.appmsgid or "").strip()
    how = ""
    tab_id, url = None, ""
    try:
        if requested_appmsgid:
            tab_id, url, how = open_existing_draft(token, requested_appmsgid)
            if how == "reopen-timeout":
                tab_id, url = None, ""
        if not tab_id and how != "reopen-appmsgid":
            if requested_appmsgid:
                print(f"  无法覆盖 appmsgid={requested_appmsgid}，改为新建一篇并注明旧编号", flush=True)
            tab_id, url, how = open_article_editor(token, home_tab)
    except RuntimeError as e:
        if str(e) == "LOGIN_REQUIRED":
            print("DRAFT_FAIL 编辑器跳转到登录页。已把窗口留在该页，请扫码后重试。")
            return 3
        raise
    if not tab_id and how != "reopen-appmsgid":
        return 3

    try:
        state = wait_article_editor(timeout_s=45)
    except RuntimeError as e:
        if str(e) == "LOGIN_REQUIRED":
            print("DRAFT_FAIL 编辑器跳转到登录页。已把窗口留在该页，请扫码后重试。")
            return 3
        raise

    url = current_url() or url or ""
    if looks_like_login():
        print("DRAFT_FAIL 编辑器跳转到登录页。已把窗口留在该页，请扫码后重试。")
        return 3
    if state.get("hasType77Drop") and not state.get("title"):
        print(f"DRAFT_FAIL 当前页像是图片帖编辑器，不是「文章」页。已停止。当前页: {url}")
        return 8
    if not is_article_editor_url(url, allow_saved=True) and not state.get("title"):
        print(f"DRAFT_FAIL 未进入 type=77&createType=0 文章编辑器。已停止。当前页: {url}")
        return 8
    if not state.get("title"):
        print(f"DRAFT_FAIL 图文编辑器控件未出现。窗口停在当前页: {url}")
        return 8
    print(f"  已进入文章编辑器（{how}） {url}", flush=True)
    dismiss_plugin_warning()

    if not fill_title(title):
        print("DRAFT_FAIL 无法写入标题，未点击保存。")
        return 8
    if args.author:
        fill_simple(["input#author", "input.js_author"], args.author.strip(), "作者")
    if digest:
        fill_simple(["textarea#js_description", "textarea.js_description", "textarea[name=digest]"], digest, "摘要")

    content = set_content(html_body)
    print(f"  正文写入: {json.dumps(content, ensure_ascii=False)}", flush=True)
    if not content.get("ok"):
        print("DRAFT_FAIL 无法定位或写入正文编辑区，未点击保存。窗口停在编辑页。")
        return 8

    cover_ok = False
    if cover:
        try:
            cover_ok = upload_cover(cover, replace=bool(requested_appmsgid))
        except Exception as e:
            print(f"  封面上传异常: {e}", flush=True)
            cover_ok = False
        if not cover_ok:
            print("  警告：封面未写入。将继续尝试保存标题与正文；配图不完整，本篇不算可交稿", flush=True)
    else:
        print("  警告：未提供封面。配图不完整，本篇不算可交稿", flush=True)

    appmsgid = None
    for click_try in range(3):
        try:
            r = click_save_draft()
        except Exception as e:
            r = {"ok": False, "reason": str(e)[:160]}
        if not (r and r.get("ok")):
            print(f"DRAFT_FAIL 未找到可点击的草稿保存按钮（第 {click_try + 1} 次）: {json.dumps(r, ensure_ascii=False)}")
            if click_try == 2:
                return 5
            time.sleep(5)
            continue
        print(f"  已点击「{r.get('label')}」", flush=True)
        for _ in range(12):
            time.sleep(2)
            try:
                m = read_appmsgid()
            except Exception:
                continue
            if m.get("id"):
                appmsgid = m["id"]
                break
        if appmsgid:
            break
        print(f"  第 {click_try + 1} 次保存未读到 appmsgid，10s 后重试", flush=True)
        time.sleep(10)
    if not appmsgid:
        print("DRAFT_FAIL 已尝试保存，但未检测到 appmsgid。窗口停在当前页，请人工确认是否已进草稿箱。")
        return 5
    print(f"  已保存 appmsgid={appmsgid}", flush=True)
    if requested_appmsgid and appmsgid == requested_appmsgid:
        print(f"  已更新原草稿 appmsgid={appmsgid}", flush=True)
    elif requested_appmsgid:
        print(f"  未能覆盖原草稿 {requested_appmsgid}，已另存为 appmsgid={appmsgid}", flush=True)

    # 保存后当前页通常已带 appmsgid，优先就地回读，避免再 navigate 超时。
    got_title = (read_title() or "").strip()
    if got_title != title:
        reopen = REOPEN.format(appmsgid=appmsgid, token=token)
        navigate_safe(reopen, timeout=90)
        time.sleep(12)
        got_title = (read_title() or "").strip()
    if got_title != title:
        print(f"DRAFT_FAIL 回读标题不匹配 got={got_title!r} expected={title!r}")
        return 6

    flag = os.path.join(os.path.dirname(html_path), f"_uploaded_{title}.flag")
    with open(flag, "w", encoding="utf-8") as ff:
        replaced = "yes" if (requested_appmsgid and appmsgid == requested_appmsgid) else (
            f"no old={requested_appmsgid}" if requested_appmsgid else "new"
        )
        ff.write(
            f"appmsgid={appmsgid}\n"
            f"session={SESSION}\n"
            f"cover={'ok' if cover_ok else ('missing' if not cover else 'not-written')}\n"
            f"entry={how}\n"
            f"replaced={replaced}\n"
        )
    cover_note = "cover=ok" if cover_ok else ("cover=missing" if not cover else "cover=not-written")
    if cover_ok:
        print(f"DRAFT_OK appmsgid={appmsgid} title={title} {cover_note}（未群发，停在发送前）")
    else:
        print(f"DRAFT_OK appmsgid={appmsgid} title={title} {cover_note}（配图不完整，本篇不算可交稿；未群发，停在发送前）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
