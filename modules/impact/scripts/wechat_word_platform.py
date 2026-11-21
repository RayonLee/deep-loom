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
    tab_id, _, _ = draft.open_article_editor("", home_tab(), reuse=False)
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
# 现行后台把「草稿箱」收进「内容管理」子菜单：不可见时先返回 expand 标记，
# 由 Python 侧对「内容管理」做一次可信点击展开后再标记「草稿箱」。
MARK_LIST = r"""(() => {
  const visible = (el) => !!el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
  document.querySelectorAll('[data-impact-word-list]').forEach(el => el.removeAttribute('data-impact-word-list'));
  const label = (el) => (el.innerText || '').trim();
  const scope = [...document.querySelectorAll('a, button, [role=menuitem], li, span, div')];
  const hits = scope.filter(el => label(el) === '草稿箱');
  const leaf = hits.filter(el => !hits.some(other => other !== el && el.contains(other)));
  const shown = leaf.filter(visible);
  if (shown.length === 1) {
    const el = shown[0];
    const link = el.matches('a[href]') ? el : el.closest('a[href]');
    let route = null;
    if (link) {
      const href = link.getAttribute('href') || '';
      if (href && !href.startsWith('#') && !/^javascript:/i.test(href)) {
        const url = new URL(href, location.href);
        if (url.origin !== location.origin || !url.pathname.startsWith('/cgi-bin/')
            || url.searchParams.has('appmsgid') || url.searchParams.get('action') === 'edit') return null;
        route = {path: url.pathname, action: url.searchParams.get('action'), view: url.searchParams.get('t')};
      }
    }
    el.setAttribute('data-impact-word-list', '1');
    return {route};
  }
  if (leaf.length && !shown.length) {
    const groups = scope.filter(el => label(el) === '内容管理' && visible(el));
    const parents = groups.filter(el => !groups.some(other => other !== el && el.contains(other)));
    if (parents.length === 1) {
      parents[0].setAttribute('data-impact-word-list', 'menu');
      return {expand: true};
    }
  }
  return null;
})()"""

# A navigation label alone is insufficient. Look only at visible draft cards
# inside the list region, and prefer a matching appmsgid exposed by a row link.
# 现行草稿箱卡片是 weui-desktop-publish__cover-*，往往不暴露 appmsgid；
# 没有编号就如实留空，由 classify_list 报告，不得伪造。
LIST_ROWS = r"""(() => {
  const visible = (el) => !!el.getClientRects().length && getComputedStyle(el).visibility === 'visible';
  const rootSelector = 'main, [role=main], #js_appmsg_list, .weui-desktop-mass-appmsg__list, .draft-list';
  const titleSelector = '.weui-desktop-publish__cover__title, .weui-desktop-mass-appmsg__title, .appmsg_card_title, .appmsg_title, .draft-card__title';
  const cardSelector = '.weui-desktop-publish__cover-item, .weui-desktop-mass-appmsg, .appmsg_card, .draft-card, li, tr, article, [role=row]';
  const knownCard = '.weui-desktop-publish__cover-item, .weui-desktop-mass-appmsg, .appmsg_card, .draft-card';
  return [...document.querySelectorAll(titleSelector)]
    .map(el => ({el, row: el.closest(cardSelector) || el}))
    .filter(({el, row}) => visible(el) && (row.closest(rootSelector) || row.matches(knownCard)))
    .map(({el, row}) => {
      const links = [...row.querySelectorAll('a[href]'), el.closest('a[href]')].filter(Boolean);
      const ids = links.map(link => new URL(link.href))
        .filter(url => url.origin === location.origin && url.pathname === '/cgi-bin/appmsg')
        .map(url => url.searchParams.get('appmsgid'));
      ids.push(row.getAttribute('data-appmsgid'), row.getAttribute('data-appmsg-id'));
      return {title: (el.innerText || '').trim(), ids: [...new Set(ids.filter(id => /^\d+$/.test(id || '')))]};
    });
})()"""

LIST_ACTIONS = {"list_draft", "draft_list", "list_card"}


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
    return (path == "/cgi-bin/appmsg" and action in LIST_ACTIONS) or path == "/cgi-bin/draft"


def classify_list(rows, title, appmsgid):
    """诚实匹配草稿箱卡片：bound=标题且编号一致；title-only=唯一同标题但卡片未暴露编号；
    ambiguous=多篇同标题或同标题卡片绑了别的编号；missing=标题不可见。"""
    matches = [row for row in rows if isinstance(row, dict) and row.get("title") == title]
    for row in matches:
        if row.get("ids") == [appmsgid]:
            return "bound"
    if len(matches) == 1 and not matches[0].get("ids"):
        return "title-only"
    if matches:
        return "ambiguous"
    return "missing"


def list_contains(rows, title, appmsgid):
    return classify_list(rows, title, appmsgid) == "bound"


def mark_list_entry():
    for _ in range(2):
        target = draft.ev(MARK_LIST, timeout=15)
        if not isinstance(target, dict):
            raise PlatformBlocked(LIST_PENDING)
        if target.get("expand"):
            draft.wb("mouse_click", {"selector": '[data-impact-word-list="menu"]'}, timeout=20)
            time.sleep(1.5)
            continue
        return target
    raise PlatformBlocked(LIST_PENDING)


def refresh_list():
    """保存前打开的列表页可能是旧快照：核验前必须刷新当前列表标签。"""
    try:
        draft.ev("location.reload()", timeout=10)
    except Exception:
        pass
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        time.sleep(1)
        try:
            if draft.ev("document.readyState", timeout=10) == "complete" and list_route(draft.current_url(), None):
                return
        except Exception:
            pass
    raise PlatformBlocked(LIST_PENDING)


def read_list_rows():
    """从官方首页点开（必要时先展开「内容管理」）并刷新草稿箱列表，只读取可见卡片。"""
    home = home_tab()
    draft.attach_tab(home)
    path, _ = official_url(draft.current_url())
    if path != "/cgi-bin/home":
        raise PlatformBlocked(LIST_PENDING)
    target = mark_list_entry()
    draft.wb("mouse_click", {"selector": '[data-impact-word-list="1"]'}, timeout=20)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for tab in draft.iter_tabs():
            tab_id = tab.get("tabId")
            if tab_id is None or not list_route(tab.get("url") or "", target.get("route")):
                continue
            draft.attach_tab(tab_id)
            if not list_route(draft.current_url(), target.get("route")):
                continue
            refresh_list()
            rows = []
            for _ in range(10):
                value = draft.ev(LIST_ROWS, timeout=15)
                rows = value if isinstance(value, list) else []
                if rows:
                    break
                time.sleep(1)
            return rows
        time.sleep(1)
    raise PlatformBlocked(LIST_PENDING)


def verify_list(title, appmsgid):
    return classify_list(read_list_rows(), title, appmsgid)


def scan_same_title(title):
    """保存前的同标题检测：只读列出草稿箱里的同标题卡片，供记录，不阻止改稿新建。"""
    return [row for row in read_list_rows() if isinstance(row, dict) and row.get("title") == title]


# 官方自动排版会把正文收进 autoTypeSetting 容器：段落/标题/图注标签被剥光，
# 内联 font-size 被剥光，只剩 section > span。检测只读，不触碰页面。
FLATTEN_CHECK = r"""(() => {
  const body = document.querySelector('.view.rich_media_content .ProseMirror, .mock-iframe-body .ProseMirror');
  if (!body) return {available: false};
  const text = (body.innerText || '').trim();
  const blocks = body.querySelectorAll('p,h1,h2,h3,h4,h5,h6,figcaption,li,blockquote,table,pre,figure').length;
  const styled = body.querySelectorAll('[style*="font-size"]').length;
  const auto = /autotypesetting/i.test(body.className || '')
    || !!body.querySelector('[class*="autotypesetting" i]');
  return {available: true, auto, blocks, styled, textLen: text.length,
          images: body.querySelectorAll('img').length};
})()"""


def is_flattened(report):
    """判定编辑区是否被官方自动排版压平。无法读取时返回 None（未知，不误报）。"""
    if not isinstance(report, dict) or not report.get("available"):
        return None
    if report.get("auto"):
        return True
    return bool(report.get("textLen")) and not report.get("blocks") and not report.get("styled")


def flatten_check():
    try:
        return is_flattened(draft.ev(FLATTEN_CHECK, timeout=15))
    except Exception:
        return None


def recover_flattened(title, digest, body, appmsgid, attempt, record):
    """首次保存被压平时，仅对本次刚新建且仍持有归属标记的同一 appmsgid 编辑页
    重写一次正文并再点一次「保存为草稿」。不属于本次新建的页面一律停止。"""
    if flatten_check() is not True:
        return
    record("saved", flatten_detected=True)
    owned = editor_url(draft.current_url(), appmsgid)
    if owned:
        try:
            require_owned(attempt, title=title, digest=digest)
        except PlatformBlocked:
            owned = False
    if not owned:
        raise PlatformBlocked("检测到官方自动排版压平正文，但已无法确认本次新建编辑页的归属，已停止。请人工检查该草稿。")
    result = draft.set_content(body, allow_short=True)
    if not isinstance(result, dict) or not result.get("ok"):
        raise PlatformBlocked("官方自动排版压平正文，重新写入正文失败。请人工检查该草稿。")
    try:
        require_owned(attempt, title=title, digest=digest)
    except PlatformBlocked:
        raise PlatformBlocked("官方自动排版压平正文，重新写入后归属核验失败，未再保存。请人工检查该草稿。") from None
    click = draft.click_save_draft()
    if not isinstance(click, dict) or click.get("ok") is not True:
        raise PlatformBlocked("官方自动排版压平正文，未能再次点击「保存为草稿」。请人工检查该草稿。")
    time.sleep(3)
    if not editor_url(draft.current_url(), appmsgid):
        raise PlatformBlocked("再次保存后编辑页编号发生变化，已停止。请人工检查草稿箱。")
    if flatten_check() is True:
        record("saved", flatten_recovered=False)
        raise PlatformBlocked("再次保存后正文仍被官方自动排版压平。请人工检查该草稿。")
    record("saved", flatten_recovered=True)


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
    ), cover_verified=bool(state.get("cover_uploaded") and page.get("hostedCover")),
        flattened=flatten_check())


LIST_AMBIGUOUS = ("官方草稿箱列表中有多篇同标题草稿，或同标题卡片绑定了其他编号，"
                  "无法确认本次草稿。保留 saved 状态，请人工核对。")
LIST_MISSING = "保存后官方草稿箱列表未见本次标题。保留 saved 状态，请人工核对。"


def finish_verification(title, state, record):
    try:
        confirm_editor(title, state["appmsgid"], state, record)
        binding = verify_list(title, state["appmsgid"])
    except PlatformBlocked:
        raise
    except Exception:
        raise PlatformBlocked(LIST_PENDING) from None
    if binding == "ambiguous":
        raise PlatformBlocked(LIST_AMBIGUOUS)
    if binding != "bound" and binding != "title-only":
        raise PlatformBlocked(LIST_MISSING)
    record("verified", list_binding=binding,
           delivery_ready=state.get("body_images_verified") is True
           and state.get("cover_verified") is True and not state.get("flattened"))


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
    # 保存前的同标题检测：只读列出草稿箱同标题卡片并记录，供人工区分「改稿新建」，
    # 不阻止保存；列表读不到时不影响主流程。扫描后必须回到本次编辑页再核验归属。
    try:
        duplicates = scan_same_title(title)
    except Exception:
        duplicates = None
    draft.attach_tab(tab_id)
    require_owned(attempt, title=title, digest=digest)
    if duplicates:
        record("blocked", checkpoint="editing", attempt_id=attempt,
               same_title_count=len(duplicates),
               same_title_ids=sorted({i for row in duplicates for i in (row.get("ids") or [])}))
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
                recover_flattened(title, digest, body, appmsgid, attempt, record)
                finish_verification(title, state, record)
                return
    raise PlatformBlocked("保存后未回读到 appmsgid。请在官方后台人工核对，自动流程不会再点保存。")


def run(title, digest, author, body, cover, expected_images, state, record, *, output):
    # Reused UI helpers log raw URLs and bridge errors for their legacy CLI. The
    # Word entry point only reports fixed messages plus its own title and ID.
    with locked_output(Path(__file__).resolve().parent), open(os.devnull, "w") as sink:
        with redirect_stdout(sink), redirect_stderr(sink):
            try:
                _run(title, digest, author, body, cover, expected_images, state, record, output)
            except PlatformBlocked:
                raise
            except Exception:
                raise PlatformBlocked("公众号页面操作未能完成，请按本地状态检查官方页面。") from None
