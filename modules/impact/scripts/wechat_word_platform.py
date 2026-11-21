"""供 Word 导入入口显式调用的 Chrome + WebBridge 草稿和回读流程。"""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import wechat_article_draft as draft
from word_to_wechat import MAX_IMAGES, locked_output

ORIGIN = "https://mp.weixin.qq.com"
DRAFT_LABEL = "草稿箱"
EDITOR_PATH = "/cgi-bin/appmsg"
EDITOR_TYPES = {"media/appmsg_edit", "media/appmsg_edit_v2"}
LIST_PENDING = "未能从官方草稿箱列表确认标题。保留 saved 状态，重跑仅回读。"
DIGEST = ["textarea#js_description", "textarea.js_description", "textarea[name=digest]"]


class PlatformBlocked(Exception):
    """只使用固定提示，避免 WebBridge 的回包或异常进入日志。"""


def official_url(url):
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.netloc == "mp.weixin.qq.com":
        return parts.path, parse_qs(parts.query, keep_blank_values=True)
    return "", {}


def editor_url(url, appmsgid=None):
    path, query = official_url(url)
    if path != EDITOR_PATH or query.get("t", [None])[0] not in EDITOR_TYPES or "share" in query:
        return False
    if appmsgid:
        return query.get("appmsgid") == [appmsgid] and query.get("createType") != ["8"]
    return (query.get("isNew") == ["1"] and query.get("type") == ["77"]
            and query.get("createType") == ["0"] and "appmsgid" not in query)


EDITOR_FIELDS = r"""(() => {
  const title = document.querySelector('textarea#title, textarea.js_title');
  const visible = document.querySelector('.title-editor__input .ProseMirror');
  const digest = document.querySelector('textarea#js_description, textarea.js_description, textarea[name=digest]');
  const author = document.querySelector('input#author, input.js_author');
  const body = document.querySelector('.view.rich_media_content .ProseMirror, .mock-iframe-body .ProseMirror');
  const cover = document.querySelector('.js_cover_preview_new, .select-cover__preview');
  const media = (src) => {
    try {
      const url = new URL(src, location.href);
      return url.protocol === 'https:' && /(^|\.)qpic\.cn$/.test(url.hostname);
    } catch { return false; }
  };
  const shown = (e) => !!e?.getClientRects().length && getComputedStyle(e).visibility === 'visible';
  const css = shown(cover) ? getComputedStyle(cover).backgroundImage : '';
  const coverURL = css.match(/^url\(["']?(.*?)["']?\)$/)?.[1];
  const images = body ? [...body.querySelectorAll('img')] : [];
  const bodyText = (body?.innerText || '').trim();
  return {
    ready: !!(title && shown(visible) && digest && shown(body)),
    title: title?.value?.trim() || '',
    visibleTitle: visible?.innerText?.trim() || '',
    digest: digest?.value?.trim() || '',
    author: author?.value?.trim() || '',
    hasBody: (!!bodyText && bodyText !== '从这里开始写正文')
      || !!body?.querySelector('img,table,video,iframe,svg,object,embed,audio,hr'),
    images: images.length,
    hosted: images.filter(i => shown(i) && i.complete && i.naturalWidth > 0 && media(i.currentSrc || i.src)).length,
    coverSet: !!coverURL,
    hostedCover: !!coverURL && media(coverURL),
    owner: document.documentElement.dataset.impactWordAttempt || ''
  };
})()"""


def fields():
    return draft.ev(EDITOR_FIELDS, timeout=15) or {}


def title_matches(page, title):
    return page.get("visibleTitle") == title and page.get("title") == title


def home_tab():
    homes = set()
    for tab in draft.iter_tabs():
        path, _ = official_url(tab.get("url") or "")
        if path == "/cgi-bin/home" and tab.get("tabId") is not None:
            homes.add(tab["tabId"])
    if len(homes) == 1:
        return homes.pop()
    if homes:
        raise PlatformBlocked("请只保留本次导入所用的官方后台首页，避免跨账号核验。")
    raise PlatformBlocked("请在 Chrome 官方页面扫码登录并打开公众号后台首页后重试。")


def looks_like_new_editor(url):
    path, query = official_url(url)
    return (path == EDITOR_PATH and query.get("t", [None])[0] in EDITOR_TYPES
            and "appmsgid" not in query and query.get("createType") != ["8"])


def owned_editor(checkpoint, attempt, title):
    tabs = [tab for tab in draft.iter_tabs() if looks_like_new_editor(tab.get("url") or "")]
    if checkpoint == "editing":
        for tab in tabs:
            if not editor_url(tab.get("url") or "") or tab.get("tabId") is None:
                continue
            draft.attach_tab(tab["tabId"])
            page = fields()
            if (editor_url(draft.current_url()) and page.get("ready") and page.get("owner") == attempt
                    and title_matches(page, title)):
                return tab["tabId"]
        raise PlatformBlocked("无法确认属于本次导入且标题一致的未保存编辑页。请先人工检查草稿箱和编辑页。")
    if tabs:
        raise PlatformBlocked("检测到已经打开的未保存文章编辑页。请先保留其中的内容并退出该页。")
    tab_id = draft.open_article_editor(home_tab(), editor_url)
    if tab_id is None:
        raise PlatformBlocked("未能从官方首页打开新的文章编辑页。")
    draft.wait_article_editor(timeout_s=35)
    page = fields()
    if (not editor_url(draft.current_url()) or not page.get("ready")
            or any(page.get(field) for field in (
                "title", "visibleTitle", "digest", "author", "hasBody", "coverSet", "owner"
            ))):
        raise PlatformBlocked("新打开的页面无法确认是空白未保存文章，平台写入已停止。")
    marking = r"""(() => {
      if (document.documentElement.dataset.impactWordAttempt) return false;
      document.documentElement.dataset.impactWordAttempt = %s;
      return true;
    })()""" % json.dumps(attempt)
    if not draft.ev(marking):
        raise PlatformBlocked("无法标记本次新开的编辑页。")
    return tab_id


def require_owned(attempt, *, title=None, digest=None):
    page = fields()
    if (not editor_url(draft.current_url()) or not page.get("ready") or page.get("owner") != attempt
            or (title is not None and not title_matches(page, title))
            or (digest is not None and page.get("digest") != digest)):
        raise PlatformBlocked("无法确认编辑页归属、标题或简介回读一致，保存已停止。")
    return page


# Only navigate by clicking the visible, exact menu label from the official home.
MARK_LIST = r"""(() => {
  const visible = (el) => !!el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
  document.querySelectorAll('[data-impact-word-list]').forEach(el => el.removeAttribute('data-impact-word-list'));
  const hits = [...document.querySelectorAll('a, button, [role=menuitem]')]
    .filter(el => (el.innerText || '').trim() === '草稿箱' && visible(el));
  const leaf = hits.filter(el => !hits.some(other => other !== el && el.contains(other)));
  if (leaf.length !== 1) return null;
  const link = leaf[0];
  const href = link.getAttribute('href');
  let route = null;
  if (href && !href.startsWith('#') && !/^javascript:/i.test(href)) {
    const url = new URL(href, location.href);
    if (url.origin !== location.origin || !url.pathname.startsWith('/cgi-bin/')
        || url.searchParams.has('appmsgid') || url.searchParams.get('action') === 'edit') return null;
    route = {path: url.pathname, action: url.searchParams.get('action'), view: url.searchParams.get('t')};
  }
  link.setAttribute('data-impact-word-list', '1');
  return {route};
})()"""

# A navigation label alone is insufficient. Look only at visible draft cards
# inside the list region, and prefer a matching appmsgid exposed by a row link.
LIST_ROWS = r"""(() => {
  const visible = (el) => !!el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
  const rootSelector = 'main, [role=main], #js_appmsg_list, .weui-desktop-mass-appmsg__list, .draft-list';
  const titleSelector = '.weui-desktop-mass-appmsg__title, .appmsg_card_title, .appmsg_title, .draft-card__title';
  const cardSelector = '.weui-desktop-mass-appmsg, .appmsg_card, .draft-card, li, tr, article, [role=row]';
  return [...document.querySelectorAll(titleSelector)]
    .filter(el => visible(el) && el.closest(rootSelector))
    .map(el => {
      const row = el.closest(cardSelector) || el;
      const links = [...row.querySelectorAll('a[href]'), el.closest('a[href]')].filter(Boolean);
      const ids = links.map(link => new URL(link.href))
        .filter(url => url.origin === location.origin && url.pathname === '/cgi-bin/appmsg')
        .map(url => url.searchParams.get('appmsgid'));
      ids.push(row.getAttribute('data-appmsgid'), row.getAttribute('data-appmsg-id'));
      return {title: (el.innerText || '').trim(), ids: [...new Set(ids.filter(id => /^\d+$/.test(id || '')))]};
    });
})()"""


def list_route(url, route):
    path, query = official_url(url)
    action = query.get("action", [None])[0]
    view = query.get("t", [None])[0]
    if not path.startswith("/cgi-bin/") or "appmsgid" in query or action == "edit":
        return False
    if path == "/cgi-bin/home" or view in EDITOR_TYPES:
        return False
    if route:
        return (path, action, view) == (route["path"], route["action"], route["view"])
    return (path == "/cgi-bin/appmsg" and action in {"list_draft", "draft_list"}) or path == "/cgi-bin/draft"


def list_contains(rows, title, appmsgid):
    return any(row.get("title") == title and row.get("ids") == [appmsgid] for row in rows)


def verify_list(title, appmsgid):
    home = home_tab()
    draft.attach_tab(home)
    path, _ = official_url(draft.current_url())
    if path != "/cgi-bin/home":
        raise PlatformBlocked(LIST_PENDING)
    target = draft.ev(MARK_LIST, timeout=15)
    if not isinstance(target, dict):
        raise PlatformBlocked(LIST_PENDING)
    before = {tab.get("tabId") for tab in draft.iter_tabs()}
    draft.wb("mouse_click", {"selector": '[data-impact-word-list="1"]'}, timeout=20)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for tab in draft.iter_tabs():
            tab_id = tab.get("tabId")
            if tab_id is None or (tab_id in before and tab_id != home):
                continue
            if list_route(tab.get("url") or "", target.get("route")):
                draft.attach_tab(tab_id)
                if list_route(draft.current_url(), target.get("route")) and list_contains(
                    draft.ev(LIST_ROWS, timeout=15) or [], title, appmsgid
                ):
                    return
        time.sleep(1)
    raise PlatformBlocked(LIST_PENDING)


def confirm_editor(title, appmsgid, state, record):
    if state.get("editor_title_confirmed"):
        return
    if not editor_url(draft.current_url(), appmsgid):
        tab = next((tab for tab in draft.iter_tabs() if editor_url(tab.get("url") or "", appmsgid)), None)
        if not tab or tab.get("tabId") is None:
            raise PlatformBlocked("已记录草稿编号。请在官方后台打开对应编辑页供只读核验，再重跑。")
        draft.attach_tab(tab["tabId"])
    page = fields()
    if not editor_url(draft.current_url(), appmsgid) or not title_matches(page, title):
        raise PlatformBlocked("已记录草稿编号，但编辑页标题尚未核对一致。")
    count = state["expected_images"]
    record("saved", editor_title_confirmed=True, body_images_verified=(
        0 < count <= MAX_IMAGES and page.get("images") == count and page.get("hosted") == count
    ), cover_verified=bool(state.get("cover_uploaded") and page.get("hostedCover")))


def finish_verification(title, state, record):
    try:
        confirm_editor(title, state["appmsgid"], state, record)
        verify_list(title, state["appmsgid"])
    except PlatformBlocked:
        raise
    except Exception:
        raise PlatformBlocked(LIST_PENDING) from None
    record("verified", delivery_ready=state.get("body_images_verified") is True and state.get("cover_verified") is True)


def _run(title, digest, author, body, cover, expected_images, state, record, output=None):
    checkpoint = state.get("checkpoint")
    if checkpoint == "save-click":
        raise PlatformBlocked("上次可能已点击保存且未拿到编号。请在官方后台人工核对，自动流程不会再新建或再点保存。")
    if not draft.ensure_extension():
        raise PlatformBlocked("未连接 Chrome 的 Kimi WebBridge。请检查扩展与官方登录状态。")
    if state.get("stage") == "saved":
        finish_verification(title, state, record)
        return

    attempt = state.get("attempt_id") if checkpoint == "editing" else uuid4().hex
    tab_id = owned_editor(checkpoint, attempt, title)
    record("blocked", checkpoint="editing", attempt_id=attempt)
    require_owned(attempt)
    if not draft.fill_title(title):
        raise PlatformBlocked("未能写入标题。")
    if author and not draft.fill_simple(["input#author", "input.js_author"], author, "作者"):
        raise PlatformBlocked("未能写入作者。")
    if not draft.fill_simple(DIGEST, digest, "简介"):
        raise PlatformBlocked("未能写入简介，正文尚未写入。")
    require_owned(attempt, title=title, digest=digest)

    result = draft.set_content(body, allow_short=True)
    if not result.get("ok") or not require_owned(attempt, title=title, digest=digest).get("hasBody"):
        raise PlatformBlocked("无法确认正文已写入。")
    cover_uploaded = False
    if cover:
        try:
            with TemporaryDirectory(dir=output, prefix=".word-cover-") as folder:
                cover_uploaded = draft.upload_cover(str(cover.resolve()), temp_dir=folder)
        except Exception:
            pass
    require_owned(attempt, title=title, digest=digest)
    record("blocked", checkpoint="save-click")
    click = draft.click_save_draft(word_attempt=attempt, word_title=title, word_digest=digest)
    if click and click.get("ok") is False and click.get("reason") in {
        "no-save-button", "disabled-save-button", "not-owned-new-page", "metadata-changed"
    }:
        record("blocked", checkpoint="editing")
        raise PlatformBlocked("没有在本次新建编辑页看到可点击的「保存为草稿」。")
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        time.sleep(1)
        for tab in draft.iter_tabs():
            if tab.get("tabId") != tab_id:
                continue
            url = tab.get("url") or ""
            _, query = official_url(url)
            appmsgid = query.get("appmsgid", [""])[0]
            if appmsgid.isascii() and appmsgid.isdigit() and editor_url(url, appmsgid):
                record("saved", checkpoint=None, attempt_id=None, appmsgid=appmsgid,
                       expected_images=expected_images, cover_uploaded=cover_uploaded)
                draft.attach_tab(tab_id)
                finish_verification(title, state, record)
                return
    raise PlatformBlocked("保存后未回读到 appmsgid。请在官方后台人工核对，自动流程不会再点保存。")


def run(title, digest, author, body, cover, expected_images, state, record, *, output):
    # Keep browser diagnostics out of CLI output. Report fixed messages and
    # the current import's title and draft ID through the entry point.
    with locked_output(Path(__file__).resolve().parent), open(os.devnull, "w") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                _run(title, digest, author, body, cover, expected_images, state, record, output)
            except PlatformBlocked:
                raise
            except Exception:
                raise PlatformBlocked("公众号页面操作未能完成，请按本地状态检查官方页面。") from None
