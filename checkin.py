#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mjjbox 自动签到（重试 3 次/每次间隔 5 分钟 / 仅最终失败通知）
说明：
 - 默认为重试次数 3（表示失败后最多重试 3 次；含首次尝试最多 4 次）
 - 每次重试间隔 300 秒（5 分钟）
 - 中间失败不通知；只有全部尝试失败后才通过 Server 酱发送最终失败通知，通知中包含每次失败原因
 - 成功时仍发送成功通知并尽量解析统计信息
用法：
  ./checkin.py --cred credentials.conf --debug
  ./checkin.py --retries 2    # 表示失败后重试 2 次（总尝试 3 次）
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


def extract_stats_from_html(html: str) -> dict:
    text = BeautifulSoup(html or "", "html.parser").get_text(separator="\n", strip=True)
    res = {"total_checkins": None, "consecutive": None, "total_points": None, "gained": None}
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
            if any(v is not None for v in stats.values()):
                if debug:
                    print(f"[debug] 从 {url} 解析到统计信息: {stats}")
                return stats
        except Exception:
            continue
    return {"total_checkins": None, "consecutive": None, "total_points": None, "gained": None}


def do_checkin_once(session: requests.Session, base_url: str, debug: bool = False) -> tuple[bool, str, dict]:
    checkin_url = urljoin(base_url, CHECKIN_PATH)
    headers = {"User-Agent": "mjjbox-checkin-bot/1.0", "Referer": base_url}
    try:
        r = session.get(checkin_url, headers=headers, timeout=15)
    except Exception as e:
        return False, f"GET {checkin_url} 失败: {e}", {}
    text_lower = (r.text or "").lower()
    if r.status_code == 200 and any(k in text_lower for k in ["签到成功", "已签到", "success", "already"]):
        stats = extract_stats_from_html(r.text)
        profile_stats = fetch_profile_stats(session, base_url, debug=debug)
        merged = {**profile_stats}
        for k, v in stats.items():
            if v is not None:
                merged[k] = v
        return True, extract_human_message(r.text) or "签到成功（从 GET 返回判断）", merged
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
    try:
        rpost = session.post(checkin_url, headers=headers, timeout=15)
    except Exception as e:
        return False, f"POST {checkin_url} 失败: {e}", {}
    tpost = (rpost.text or "").lower()
    if any(k in tpost for k in ["签到成功", "已签到", "success"]):
        stats = extract_stats_from_html(rpost.text)
        profile_stats = fetch_profile_stats(session, base_url, debug=debug)
        merged =
