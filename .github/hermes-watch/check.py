#!/usr/bin/env python3
"""Hermes Mobile beta watch: curl-only checks for GitHub Actions.

Checks, every run, from the public landing page (re-extracted each time):
  1. Landing page HTML -> current Android (Firebase) href + iOS TestFlight code.
  2. TestFlight join URL(s) -> HTTP status only (404 = invalid/removed).
     A 200 means the page is reachable; it does NOT by itself prove a slot
     is open. A human/browser check on a phone is still required.
  3. Firebase join URL -> unauthenticated request only. It redirects to
     Google sign-in, so curl can NEVER tell whether testers are at the
     limit. Always recorded as "unverified_login_required". The 30-minute
     browser recheck remains the only Firebase verdict.
  4. GitHub issue #147269 -> updated_at / comment count via public API.
     A new comment is a "watch_only" signal: it needs a browser check,
     it is not proof of an open slot.

No accounts, passwords, tokens or personal data are used or stored.
State lives in state.json next to this script; only transitions vs the
previous state count as a change, so a steady 404 does not re-alert.
Exit codes: 0 = checked (changed or not), 2 = landing page unreachable
(no state overwrite, no notification; the next run retries).
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

LANDING_URL = "https://hermes-agent.nousresearch.com/mobile"
ISSUE_API = "https://api.github.com/repos/NousResearch/hermes-agent/issues/147269"

DESKTOP_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

FIREBASE_RE = re.compile(r"https://appdistribution\.firebase\.dev/i/([A-Za-z0-9_-]+)")
TESTFLIGHT_RE = re.compile(r"https://testflight\.apple\.com/join/([A-Za-z0-9]+)")


def beijing_now():
    return datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=8)))


def fetch_text(url, user_agent, timeout=20):
    """GET with redirects followed; returns (status, final_url, text)."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(200_000)
            return resp.status, resp.geturl(), raw.decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        raw = exc.read(200_000)
        return exc.code, exc.geturl(), raw.decode("utf-8", "ignore")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_no_redirect(url, user_agent, timeout=20):
    """GET without following redirects; returns (status, location, text)."""
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.headers.get("Location", ""), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location", ""), ""


def classify_testflight(status, body):
    if status == 404:
        return "invalid_or_removed"
    if status == 200:
        lowered = body.lower()
        if "beta is full" in lowered or "this beta is full" in lowered:
            return "reachable_beta_full_text"
        return "reachable"
    if status is None:
        return "check_error"
    return "unexpected_status_%s" % status


def check_testflight(code):
    url = "https://testflight.apple.com/join/%s" % code
    try:
        status, final_url, body = fetch_text(url, IPHONE_UA, timeout=20)
    except Exception as exc:  # network/timeout: not a status change
        return {"code": code, "url": url, "http_status": None,
                "classification": "check_error", "error": str(exc)[:200]}
    snippet = re.sub(r"\s+", " ", body)[:300]
    return {"code": code, "url": url, "http_status": status,
            "final_url": final_url,
            "classification": classify_testflight(status, body),
            "body_snippet": snippet}


def check_firebase(href):
    try:
        status, location, _ = fetch_no_redirect(href, DESKTOP_UA, timeout=20)
    except Exception as exc:
        return {"href": href, "http_status": None,
                "classification": "check_error", "error": str(exc)[:200]}
    host = ""
    if location:
        host = urllib.request.urlparse(location).netloc
    # Whatever the redirect is, curl without a signed-in account cannot
    # see the tester-limit state. Never upgrade this to full/available.
    return {"href": href, "http_status": status, "redirect_location": location,
            "redirect_host": host,
            "classification": "unverified_login_required"}


def check_issue():
    headers = {"User-Agent": "hermes-watch", "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        # Only for the public issue read, to avoid the unauthenticated
        # 60/hour limit. The token value is never printed or stored.
        headers["Authorization"] = "Bearer %s" % token
    req = urllib.request.Request(ISSUE_API, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read(50_000).decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        return {"status": "http_error_%s" % exc.code, "updated_at": None,
                "comments": None}
    except Exception as exc:
        return {"status": "check_error", "error": str(exc)[:200],
                "updated_at": None, "comments": None}
    return {"status": "ok", "updated_at": data.get("updated_at"),
            "comments": data.get("comments"), "title": data.get("title"),
            "html_url": data.get("html_url")}


def load_state(path):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True)
    parser.add_argument("--old-code", default="U8e1zmbc")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    now_bj = beijing_now()

    # 1. Landing page. A failure here aborts without overwriting state.
    try:
        status, _final, html = fetch_text(LANDING_URL, DESKTOP_UA, timeout=25)
    except Exception as exc:
        print("landing fetch failed: %s" % exc, file=sys.stderr)
        return 2
    if status != 200:
        print("landing http status %s, aborting" % status, file=sys.stderr)
        return 2

    firebase_match = FIREBASE_RE.search(html)
    testflight_match = TESTFLIGHT_RE.search(html)
    if not firebase_match or not testflight_match:
        print("landing page: could not extract both hrefs", file=sys.stderr)
        return 2
    firebase_id = firebase_match.group(1)
    firebase_href = "https://appdistribution.firebase.dev/i/%s" % firebase_id
    tf_code = testflight_match.group(1)
    tf_href = "https://testflight.apple.com/join/%s" % tf_code
    has_reserve = "reserve your spot" in html.lower()

    # 2. TestFlight: current code, plus the old code if it differs.
    tf_results = {tf_code: check_testflight(tf_code)}
    if args.old_code and args.old_code != tf_code:
        tf_results[args.old_code] = check_testflight(args.old_code)

    # 3. Firebase: unauthenticated only, always unverified.
    firebase = check_firebase(firebase_href)

    # 4. GitHub issue (best effort; failure never blocks the other checks).
    issue = check_issue()

    new_state = {
        "version": 1,
        "checked_at_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "checked_at_beijing": now_bj.strftime("%Y-%m-%d %H:%M:%S"),
        "landing": {
            "url": LANDING_URL, "http_status": status,
            "firebase_id": firebase_id, "firebase_href": firebase_href,
            "testflight_code": tf_code, "testflight_href": tf_href,
            "has_reserve_text": has_reserve,
        },
        "testflight": tf_results,
        "firebase": firebase,
        "github_issue": issue,
    }

    old = load_state(args.state)
    changes = []
    kinds = set()
    if old is not None:
        old_landing = old.get("landing", {})
        if old_landing.get("testflight_code") and old_landing["testflight_code"] != tf_code:
            changes.append("落地页 TestFlight 邀请码变化：%s -> %s"
                           % (old_landing["testflight_code"], tf_code))
            kinds.add("slot_signal")
        if old_landing.get("firebase_href") and old_landing["firebase_href"] != firebase_href:
            changes.append("落地页安卓 Firebase 链接变化：%s -> %s"
                           % (old_landing["firebase_href"], firebase_href))
            kinds.add("slot_signal")
        for code, result in tf_results.items():
            prev = (old.get("testflight", {}) or {}).get(code, {})
            prev_status = prev.get("http_status")
            new_status = result.get("http_status")
            if prev_status in (200, 404) and new_status in (200, 404) and prev_status != new_status:
                changes.append("TestFlight %s 状态变化：HTTP %s -> %s（%s -> %s）"
                               % (code, prev_status, new_status,
                                  prev.get("classification"), result["classification"]))
                kinds.add("slot_signal")
        old_issue = old.get("github_issue", {}) or {}
        if (issue.get("status") == "ok" and old_issue.get("updated_at")
                and issue.get("updated_at") != old_issue["updated_at"]):
            changes.append("GitHub issue #147269 有更新：%s -> %s，需浏览器核实"
                           % (old_issue["updated_at"], issue["updated_at"]))
            kinds.add("watch_only")
    else:
        changes.append("首次建基线（落地页邀请码 %s，TestFlight HTTP %s）"
                       % (tf_code, tf_results[tf_code].get("http_status")))

    if old is None:
        changed = False
        change_kind = "baseline"
    elif changes:
        changed = True
        change_kind = "slot_signal" if "slot_signal" in kinds else "watch_only"
    else:
        changed = False
        change_kind = "none"

    with open(args.state, "w", encoding="utf-8") as handle:
        json.dump(new_state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    if change_kind == "slot_signal":
        headline = "Hermes 盯梢：出现需核实的信号变化（curl 只能看到链接状态，不能直接认定有名额）"
    elif change_kind == "watch_only":
        headline = "Hermes 盯梢：issue 有更新，需浏览器核实"
    elif change_kind == "baseline":
        headline = "Hermes 盯梢：已建立首次基线，无需处理"
    else:
        headline = "Hermes 盯梢：无变化"
    lines = [headline, "检查时间（北京）：%s" % new_state["checked_at_beijing"],
             "落地页邀请码：%s" % tf_code,
             "TestFlight %s：HTTP %s（%s）"
             % (tf_code, tf_results[tf_code].get("http_status"),
                tf_results[tf_code]["classification"]),
             "安卓 Firebase：curl 未登录，只能记为未验证，需 30 分钟浏览器复查。",
             "新链接：%s" % tf_href]
    if changes:
        lines.append("变化：")
        lines.extend("- %s" % item for item in changes)
    message = "\n".join(lines)

    print(message)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("## Hermes watch\n\n")
            handle.write("```\n%s\n```\n" % message)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write("changed=%s\n" % ("true" if changed else "false"))
            handle.write("change_kind=%s\n" % change_kind)
            handle.write("message<<HERMES_MSG_EOF\n%s\nHERMES_MSG_EOF\n" % message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
