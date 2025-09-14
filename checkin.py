#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mjjbox 自动签到脚本（增强版）
功能增加：
 - 支持 Server 酱 (经典 & Turbo) 通知
 - 签到失败自动重试 3 次（每次失败均通知）
 - 成功通知中尽量显示：已签到次数 / 连续签到 / 总积分 / 本次获得积分
用法示例：
  ./checkin.py --cred credentials.conf --debug
"""

from __future__ import annotations
import os
import sys
import time
import argparse
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

DEFAULT_BASE = "https://mjjbox.com"
LOGIN_PATH = "/login"
CHECKIN_PATH = "/checkin"
DEFAULT_CRED = "credentials.conf"

USER_HINTS = ["user", "username", "email", "login", "account"]
PASS_HINTS = ["pass", "password", "passwd", "pwd"]

# profile page candidates to try for stats scraping
PROFILE_PATHS = ["/user", "/user/profile", "/profile", "/member", "/my", "/home", "/dashboard"]


def load_credentials(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"credentials file not found: {path}")
    data = {}
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    if not data:
        raise ValueError("credentials file is empty")
    base = data.get("base", DEFAULT_BASE)
    username = data.get("username") or data.get("email") or next((v for k, v in data.items() if k.lower() not in ("password", "passwd", "pass", "serverchan", "base")), None)
    password = data.get("password") or data.get("passwd") or data.get("pass")
    serverchan = data.get("serverchan") or ""
    if not username or not password:
        raise ValueError("credentials must contain username (or email) and password")
    return {"username": username, "password": password, "serverchan": serverchan, "base": base}


# ----------------- 登录与签到 (与之前逻辑兼容) -----------------
def find_login_form(soup: BeautifulSoup):
    forms = soup.find_all("form")
    for form in forms:
        inputs = form.find_all("input")
        uname = None
        pwd = None
        hidden = {}
        for inp in inputs:
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ == "hidden":
                hidden[name] = inp.get("value", "")
                continue
            lname = name.lower()
            if typ == "password" or any(h in lname for h in PASS_HINTS):
                pwd = name
            if typ in ("text", "email") or any(h in lname for h in USER_HINTS):
                if not uname:
                    uname = name
        if pwd:
            return form, uname, pwd, hidden
    return None, None, None, {}


def build_payload_from_form(form, username_name: str, password_name: str, username_value: str, password_value: str, hidden_fields: dict):
    payload = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "text").lower()
        if typ in ("submit", "button"):
            continue
        if name == username_name:
            payload[name] = username_value
        elif name == password_name:
            payload[name] = password_value
        else:
            payload[name] = inp.get("value", "") or ""
    for k, v in hidden_fields.items():
        if k not in payload:
            payload[k] = v
    return payload


def heuristic_payload(username_value: str, password_value: str):
    combos = []
    for u in USER_HINTS:
        for p in PASS_HINTS:
            combos.append({u: username_value, p: password_value})
    return combos


def try_login(session: requests.Session, base_url: str, username: str, password: str, debug: bool = False) -> bool:
    login_url = urljoin(base_url, LOGIN_PATH)
    headers = {"User-Agent": "mjjbox-checkin-bot/1.0", "Referer": base_url}
    try:
        r = session.get(login_url, headers=headers, timeout=15)
    except Exception as e:
        if debug:
            print("[debug] GET login error:", e)
        return False

    soup = BeautifulSoup(r.text or "", "html.parser")
    form, uname_field, pwd_field, hidden = find_login_form(soup)
    if form:
        action = form.get("action") or LOGIN_PATH
        post_url = urljoin(login_url, action)
        if not uname_field:
            for inp in form.find_all("input"):
                n = inp.get("name")
                if not n:
                    continue
                t = (inp.get("type") or "text").lower()
                if t in ("text", "email") and n != pwd_field:
                    uname_field = n
                    break
        if not uname_field:
            for inp in form.find_all("input"):
                n = inp.get("name")
                if not n: continue
                if any(h in n.lower() for h in USER_HINTS):
                    uname_field = n
                    break
        if not pwd_field:
            for inp in form.find_all("input"):
                if (inp.get("type") or "").lower() == "password":
                    pwd_field = inp.get("name")
                    break
        if pwd_field:
            payload = build_payload_from_form(form, uname_field, pwd_field, username, password, hidden)
            if debug:
                print(f"[debug] POST login to {post_url} fields: {list(payload.keys())[:8]}")
            try:
                r2 = session.post(post_url, data=payload, headers=headers, timeout=15, allow_redirects=True)
            except Exception as e:
                if debug:
                    print("[debug] POST login failed:", e)
                r2 = None
            if r2 is not None:
                txt = (r2.text or "").lower()
                if any(k in txt for k in ["logout", "sign out", "登出", "退出", "个人资料", "profile"]):
                    return True
                try:
                    home = session.get(base_url, headers=headers, timeout=10)
                    htxt = (home.text or "").lower()
                    if any(k in htxt for k in ["logout", "sign out", "登出", "退出", "个人资料", "profile"]):
                        return True
                except Exception:
                    pass
    # fallback combos
    for combo in heuristic_payload(username, password):
        try:
            r3 = session.post(login_url, data=combo, headers=headers, timeout=15, allow_redirects=True)
        except Exception:
            continue
        txt3 = (r3.text or "").lower()
        if any(k in txt3 for k in ["logout", "sign out", "登出", "退出", "个人资料", "profile"]):
            return True
        try:
            home = session.get(base_url, headers=headers, timeout=10)
            htxt = (home.text or "").lower()
            if any(k in htxt for k in ["logout", "sign out", "登出", "退出", "个人资料", "profile"]):
                return True
        except Exception:
            pass
    return False


# ----------------- 解析统计信息 (启发式) -----------------
def extract_stats_from_html(html: str) -> dict:
    """
    尝试从 html 文本中抽取以下信息：
      - total_checkins (已签到次数)
      - consecutive (连续签到天数)
      - total_points (总积分)
      - gained (本次签到获得积分)
    返回字典（若无法解析字段则对应值为 None）
    """
    text = BeautifulSoup(html or "", "html.parser").get_text(separator="\n", strip=True)
    res = {"total_checkins": None, "consecutive": None, "total_points": None, "gained": None}
    # 常见中文/英文正则
    patterns = {
        "total_checkins": [
            r"已签到(?:\s*[:：]?)\s*(\d+)",
            r"累计签到(?:\s*[:：]?)\s*(\d+)",
            r"total\s*checkins?\s*[:：]?\s*(\d+)"
        ],
        "consecutive": [
            r"连续签到(?:\s*[:：]?)\s*(\d+)\s*天",
            r"连续(?:签到)?\s*(\d+)\s*天",
            r"consecutive\s*days?\s*[:：]?\s*(\d+)"
        ],
        "total_points": [
            r"(?:积分|点数|score|points?)\s*[:：]?\s*(\d+)",
            r"总积分(?:\s*[:：]?)\s*(\d+)",
            r"balance\s*[:：]?\s*(\d+)"
        ],
        "gained": [
            r"本次签到(?:获得|奖励|奖励了)?\s*(\d+)\s*(?:积分|点)",
            r"获得(?:了)?\s*(\d+)\s*(?:积分|points?)",
            r"you gained\s*(\d+)\s*points?"
        ],
    }
    for key, regs in patterns.items():
        for reg in regs:
            m = re.search(reg, text, re.IGNORECASE)
            if m:
                try:
                    val = int(m.group(1))
                    res[key] = val
                    break
                except Exception:
                    continue
    # 进一步尝试：有些站点写成 "签到数 123 次" 或 "签到:123"
    if res["total_checkins"] is None:
        m = re.search(r"签到[^\d]{0,4}(\d+)", text)
        if m:
            res["total_checkins"] = int(m.group(1))
    return res


def fetch_profile_stats(session: requests.Session, base_url: str, debug: bool = False) -> dict:
    for p in PROFILE_PATHS:
        url = urljoin(base_url, p)
        try:
            r = session.get(url, timeout=12)
            if r.status_code != 200:
                continue
            stats = extract_stats_from_html(r.text)
            # if some useful stats found, return them (even partial)
            if any(v is not None for v in stats.values()):
                if debug:
                    print(f"[debug] 从 {url} 解析到统计信息: {stats}")
                return stats
        except Exception:
            continue
    return {"total_checkins": None, "consecutive": None, "total_points": None, "gained": None}


# ----------------- 签到调用（单次） -----------------
def do_checkin_once(session: requests.Session, base_url: str, debug: bool = False) -> tuple[bool, str, dict]:
    """
    单次尝试签到。返回 (success(bool), message(str), stats(dict or {}))
    stats 是函数 extract_stats_from_html() 的结果（可能为空/部分）
    """
    checkin_url = urljoin(base_url, CHECKIN_PATH)
    headers = {"User-Agent": "mjjbox-checkin-bot/1.0", "Referer": base_url}
    try:
        r = session.get(checkin_url, headers=headers, timeout=15)
    except Exception as e:
        return False, f"GET {checkin_url} 失败: {e}", {}
    text_lower = (r.text or "").lower()
    # 直接判断页面中是否包含成功/已签到关键字
    if r.status_code == 200 and any(k in text_lower for k in ["签到成功", "已签到", "success", "already"]):
        stats = extract_stats_from_html(r.text)
        # 尝试进一步从 profile 获取更完整的 stats（可选）
        profile_stats = fetch_profile_stats(session, base_url, debug=debug)
        # 合并：优先用 checkin 页面解析到的 gained；其他用 profile
        merged = {**profile_stats}
        for k, v in stats.items():
            if v is not None:
                merged[k] = v
        return True, extract_human_message(r.text) or "签到成功（从 GET 返回判断）", merged
    # 若有 form，尝试提交
    soup = BeautifulSoup(r.text or "", "html.parser")
    form = soup.find("form")
    if form and form.get("action"):
        action = form.get("action")
        post_url = urljoin(checkin_url, action)
        payload = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if not name:
                continue
            typ = (inp.get("type") or "text").lower()
            if typ in ("submit", "button"):
                continue
            payload[name] = inp.get("value", "") or ""
        try:
            r2 = session.post(post_url, data=payload, headers=headers, timeout=15)
        except Exception as e:
            return False, f"POST {post_url} 失败: {e}", {}
        txt2 = (r2.text or "").lower()
        if any(k in txt2 for k in ["签到成功", "已签到", "success"]):
            stats = extract_stats_from_html(r2.text)
            profile_stats = fetch_profile_stats(session, base_url, debug=debug)
            merged = {**profile_stats}
            for k, v in stats.items():
                if v is not None:
                    merged[k] = v
            return True, extract_human_message(r2.text) or "签到成功（提交表单）", merged
        return False, extract_human_message(r2.text) or f"表单提交后未检测到成功关键词（HTTP {r2.status_code}）", {}
    # 最后尝试直接 POST /checkin
    try:
        rpost = session.post(checkin_url, headers=headers, timeout=15)
    except Exception as e:
        return False, f"POST {checkin_url} 失败: {e}", {}
    tpost = (rpost.text or "").lower()
    if any(k in tpost for k in ["签到成功", "已签到", "success"]):
        stats = extract_stats_from_html(rpost.text)
        profile_stats = fetch_profile_stats(session, base_url, debug=debug)
        merged = {**profile_stats}
        for k, v in stats.items():
            if v is not None:
                merged[k] = v
        return True, extract_human_message(rpost.text) or "签到成功（POST）", merged
    return False, extract_human_message(rpost.text) or f"签到返回 HTTP {rpost.status_code}", {}


def extract_human_message(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    texts = []
    for tag in soup.find_all(["p", "div", "span", "strong", "li"]):
        t = tag.get_text(strip=True)
        if not t:
            continue
        if 2 < len(t) < 400:
            texts.append(t)
    for t in texts:
        low = t.lower()
        if any(k in low for k in ["签到", "成功", "已签到", "失败", "请登录", "success", "already"]):
            return t
    return texts[0] if texts else ""


# ----------------- Server 酱通知 -----------------
def send_serverchan(sckey: str, title: str, desp: str, debug: bool = False) -> bool:
    if not sckey:
        return False
    sckey = sckey.strip()
    try:
        if sckey.upper().startswith("SCT"):
            url = f"https://sct.ftqq.com/{sckey}.send"
            params = {"title": title, "desp": desp}
        else:
            url = f"https://sc.ftqq.com/{sckey}.send"
            params = {"text": title, "desp": desp}
        if debug:
            print("[debug] Server 酱 请求 URL:", url)
        r = requests.post(url, data=params, timeout=10)
        if debug:
            print("[debug] Server 酱返回:", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as e:
        if debug:
            print("[debug] send_serverchan 异常:", e)
        return False


# ----------------- 带重试的主流程 -----------------
def checkin_with_retries(session: requests.Session, base: str, serverchan_key: str, username: str, retries: int = 3, debug: bool = False) -> int:
    """
    执行签到，失败时自动重试最多 retries 次（含第一次）。每次失败都会发送 Server 酱通知（如果配置）。
    返回 0 成功，1 最终失败（所有重试均失败），2 凭据/参数错误等（上层决定）
    """
    attempt = 0
    failure_reasons = []
    while attempt < retries:
        attempt += 1
        if debug:
            print(f"[debug] 签到尝试 {attempt}/{retries} ...")
        success, msg, stats = do_checkin_once(session, base, debug=debug)
        if success:
            # 构建成功通知内容（尽量包含统计）
            t = "mjjbox 签到成功 ✅"
            desp_lines = []
            desp_lines.append(f"用户: {username}")
            desp_lines.append(f"结果: 签到成功")
            desp_lines.append(f"详情: {msg}")
            # 统计信息
            if stats:
                sc = stats.get("total_checkins")
                cd = stats.get("consecutive")
                tp = stats.get("total_points")
                gained = stats.get("gained")
                if sc is not None:
                    desp_lines.append(f"已签到次数: {sc}")
                if cd is not None:
                    desp_lines.append(f"连续签到: {cd} 天")
                if tp is not None:
                    desp_lines.append(f"总积分: {tp}")
                if gained is not None:
                    desp_lines.append(f"本次获得: {gained} 分")
            desp = "\n".join(desp_lines)
            if serverchan_key:
                send_serverchan(serverchan_key, t, desp, debug=debug)
            # 成功退出
            return 0
        else:
            # 失败，记录原因并通知（每次失败都通知）
            reason = msg or "未获得失败原因"
            failure_reasons.append(f"尝试#{attempt}: {reason}")
            t = f"mjjbox 签到尝试 {attempt} 失败 ❌"
            desp = f"用户: {username}\n尝试: {attempt}/{retries}\n原因: {reason}\n站点: {base}\n"
            if serverchan_key:
                send_serverchan(serverchan_key, t, desp, debug=debug)
            # 若未达到重试上限，则等待后重试
            if attempt < retries:
                if debug:
                    print(f"[debug] 等待 3 秒后重试...")
                time.sleep(3)
            else:
                # 所有尝试结束，整体失败，发最终通知（包含各次原因）
                t2 = "mjjbox 签到最终失败 ❌"
                desp2 = f"用户: {username}\n结果: 最终失败（{retries} 次尝试均失败）\n站点: {base}\n\n每次原因汇总:\n" + "\n".join(failure_reasons)
                if serverchan_key:
                    send_serverchan(serverchan_key, t2, desp2, debug=debug)
                return 1
    # unreachable
    return 1


def main():
    parser = argparse.ArgumentParser(description="mjjbox 自动签到（重试 + Server 酱通知 + 统计解析）")
    parser.add_argument("--cred", "-c", default=DEFAULT_CRED, help="credentials 文件路径（默认 credentials.conf）")
    parser.add_argument("--base", "-b", default=DEFAULT_BASE, help="站点基地址（默认 https://mjjbox.com）")
    parser.add_argument("--retries", "-r", type=int, default=3, help="签到失败时最多重试次数（含第一次），默认 3")
    parser.add_argument("--debug", action="store_true", help="打印调试信息")
    args = parser.parse_args()

    try:
        cred = load_credentials(args.cred)
    except Exception as e:
        print("读取凭据失败:", e)
        sys.exit(2)

    username = cred["username"]
    password = cred["password"]
    serverchan_key = cred.get("serverchan", "").strip()
    base = cred.get("base", args.base) or args.base

    session = requests.Session()
    session.headers.update({"User-Agent": "mjjbox-checkin-bot/1.0"})

    # 先尝试登录（若不需要也不会影响：某些站点允许未登录签到）
    if args.debug:
        print("[debug] 尝试登录...")
    try:
        logged_in = try_login(session, base, username, password, debug=args.debug)
    except Exception as e:
        logged_in = False
        if args.debug:
            print("[debug] 登录时异常:", e)

    if args.debug and not logged_in:
        print("[debug] 登录未确认，仍将尝试签到（部分站点无需登录）")

    # 执行带重试的签到主流程
    code = checkin_with_retries(session, base, serverchan_key, username, retries=args.retries, debug=args.debug)
    sys.exit(code)


if __name__ == "__main__":
    main()
