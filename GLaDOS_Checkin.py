# -*- coding: utf-8 -*-
# 用于 GitHub Actions（也可本地运行）
#
# 功能：
#   1. 支持一个或多个 GLaDOS 账号签到（每个账号独立 Cookie、独立兑换计划）
#   2. 每个账号的签到结果「分别」发送邮件，可发给一个或多个收件人
#   3. 发信支持 SMTP（推荐，QQ/163/Gmail 等普通邮箱授权码，收件人不受限）
#      和 Resend API（可选，未验证自有域名时只能发给注册 Resend 用的那个邮箱）
#   4. 配置既可以用「一个 secret 搞定」（GLADOS_CONFIG），也可以拆成一组 secrets
#
# 详细配置说明见 README.md
from __future__ import annotations

import hashlib
import os
import smtplib
import sys
from dataclasses import dataclass, field
from datetime import datetime
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional, Tuple

import requests

# ========== API 地址与常量 ==========
CHECKIN_URL = "https://glados.one/api/user/checkin"
STATUS_URL = "https://glados.one/api/user/status"
POINTS_URL = "https://glados.one/api/user/points"
EXCHANGE_URL = "https://glados.one/api/user/exchange"

RESEND_API_URL = "https://api.resend.com/emails"

# GLaDOS 会把「登录设备」和当前请求的设备做比对（按 User-Agent 判断），不一致就直接拒绝，
# 签到接口返回 code 4 + "Automated check-in detected"。所以这里默认用一个 Windows + Edge 的 UA，
# 和多数人登录时用的浏览器一致。如果你是用手机或 Chrome 登录的，用环境变量 GLADOS_USER_AGENT
# 或配置里的 user_agent 覆盖成你自己浏览器的 UA。
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"
)
USER_AGENT = DEFAULT_USER_AGENT

# 兑换计划：名称 -> (所需积分, 兑换天数)
PLAN_MAP = {
    "plan100": (100, 10),
    "plan200": (200, 30),
    "plan500": (500, 100),
}
DEFAULT_PLAN = "plan500"

DEFAULT_FROM_NAME = "GLaDOS_Checkin"
DEFAULT_RESEND_FROM = f"{DEFAULT_FROM_NAME} <onboarding@resend.dev>"
DEFAULT_SMTP_SERVER = "smtp.qq.com"
DEFAULT_SMTP_PORT = "465"
# ==================================


# ---------------------------------------------------------------- 配置结构
@dataclass
class MailSettings:
    """发信配置（全局默认值 + 单账号覆盖，合并后的结果）"""

    provider: str = ""       # resend / smtp
    api_key: str = ""        # Resend API Key
    sender: str = ""         # 发件人，可写成 "名字 <地址>" 或裸地址
    smtp_server: str = ""
    smtp_port: str = ""
    smtp_user: str = ""
    smtp_pass: str = ""

    def finalize(self) -> "MailSettings":
        """推断 provider、补齐默认值，返回自身"""
        if self.provider:
            if self.provider not in ("resend", "smtp"):
                raise ValueError(f"provider 只能是 resend 或 smtp，当前为：{self.provider}")
        elif self.api_key and not self.smtp_user:
            self.provider = "resend"
        elif self.smtp_user:
            self.provider = "smtp"
        else:
            self.provider = "resend" if self.api_key else "smtp"

        if self.provider == "resend":
            if not self.sender:
                self.sender = DEFAULT_RESEND_FROM
            self.sender = _normalize_from(self.sender)
        else:
            self.smtp_server = self.smtp_server or DEFAULT_SMTP_SERVER
            self.smtp_port = self.smtp_port or DEFAULT_SMTP_PORT
            if not self.sender:
                self.sender = self.smtp_user
            self.sender = _normalize_from(self.sender)
        return self

    def problems(self) -> List[str]:
        """返回配置缺失项（用于给出人话报错）"""
        missing = []
        if self.provider == "resend":
            if not self.api_key:
                missing.append("api_key（Resend API Key）")
            if not self.sender:
                missing.append("from（发件人）")
        else:
            if not self.smtp_user:
                missing.append("user（SMTP 登录账号）")
            if not self.smtp_pass:
                missing.append("pass（SMTP 授权码/密码）")
            if not self.smtp_server:
                missing.append("smtp_server")
        return missing


@dataclass
class Account:
    cookie: str
    name: str = ""
    plan: str = ""
    mail_to: List[str] = field(default_factory=list)
    mail_raw: Dict[str, str] = field(default_factory=dict)

    def label(self, index: int, total: int) -> str:
        """账号显示名：优先用配置里的名字，多账号时自动补「账号N」"""
        if self.name:
            return self.name
        return f"账号{index}" if total > 1 else ""


def _normalize_from(value: str) -> str:
    """把发件人统一成 `名字 <地址>` 形式；Resend 与 SMTP 都适用"""
    value = (value or "").strip()
    if not value:
        return ""
    if "<" in value and ">" in value:
        return value
    return f"{DEFAULT_FROM_NAME} <{value}>"


def _pick(raw: Dict[str, str], *keys: str) -> str:
    for k in keys:
        v = raw.get(k)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _split_list(value: str) -> List[str]:
    """收件人等多值字段拆分：支持逗号、分号、空白分隔，自动去重"""
    if not value:
        return []
    for sep in (";", "\n", " ", "\t"):
        value = value.replace(sep, ",")
    result = []
    for part in value.split(","):
        part = part.strip()
        if part and part not in result:
            result.append(part)
    return result


def _split_cookies(value: str) -> List[str]:
    """全局 cookies 字段：支持 && 分隔多个 Cookie"""
    if not value:
        return []
    if "&&" in value:
        return [c.strip() for c in value.split("&&") if c.strip()]
    return [value.strip()] if value.strip() else []


def _strip_inline_comment(line: str) -> str:
    """去掉行尾注释（# 前必须有空白，避免误伤 Cookie/邮箱里的字符）"""
    for i, ch in enumerate(line):
        if ch == "#" and i > 0 and line[i - 1].isspace():
            return line[:i].strip()
    return line.strip()


# 配置文件里认识的键名。[account] 段里出现「不认识、又长得像 Cookie」的行时，
# 按「直接粘贴的 Cookie」处理——用户经常忘了在 Cookie 前面写 `cookie = `。
KNOWN_CONFIG_KEYS = {
    "name", "label", "remark", "note", "备注",
    "cookie", "cookies", "glados_cookie", "glados_cookies", "token",
    "mail_to", "to", "email", "emails", "mail", "收件邮箱",
    "plan", "exchange_plan",
    "provider", "mail_provider", "api_key", "resend_api_key", "mail_api_key",
    "from", "mail_from", "sender", "mail_sender",
    "smtp_server", "smtp_host", "server", "smtp_port", "port",
    "user", "smtp_user", "mail_user",
    "pass", "password", "smtp_pass", "mail_pass", "auth_code", "authorization_code",
    "user_agent", "ua",
}
# GLaDOS 登录后的会话 Cookie 名（成对出现，缺一个就是未登录）。
# 注意：GLaDOS 现已改用 gld:sess 判断登录态，只带老式的 koa:sess 一定返回「没有权限」
# （实测：有效 koa:sess 单独发送 → 没有权限；有效 gld:sess 单独发送 → 正常返回账号数据）。
SESSION_COOKIE_NAME = "gld:sess"
LEGACY_COOKIE_NAME = "koa:sess"
SESSION_COOKIE_NAMES = (SESSION_COOKIE_NAME, LEGACY_COOKIE_NAME)


def check_cookie_shape(cookie: str) -> List[str]:
    """
    检查 Cookie 串自身的常见问题，返回人类可读的警告列表。
    Cookie 没通过认证时，问题经常出在复制环节而不是账号本身，这里先把这些坑挑出来。
    """
    cookie = cookie or ""
    warnings: List[str] = []

    if f"{SESSION_COOKIE_NAME}=" not in cookie:
        warnings.append(
            f"Cookie 里没有 {SESSION_COOKIE_NAME}：GLaDOS 现在按 {SESSION_COOKIE_NAME} 判断登录态，"
            f"只有 {LEGACY_COOKIE_NAME} 无法通过认证，请重新登录后复制包含 {SESSION_COOKIE_NAME} 的完整 Cookie"
        )
    elif f"{SESSION_COOKIE_NAME}.sig" not in cookie:
        warnings.append(f"缺少 {SESSION_COOKIE_NAME}.sig：签名 Cookie 必须成对，少一个服务端一定判为未登录")

    # 同一个名字出现两次且值不同 = 两次复制混在了一起
    seen: Dict[str, str] = {}
    for part in cookie.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name = name.strip()
        if name in seen and seen[name] != value:
            warnings.append(f"{name} 出现了两次且值不同：Cookie 是两次复制混在一起的，要重新完整复制一次")
        seen[name] = value

    # 少写分隔符，两条 Cookie 粘在一起（浏览器界面折行时复制容易出这个问题）
    for name in SESSION_COOKIE_NAMES:
        start = 0
        glued = False
        while True:
            idx = cookie.find(name, start)
            if idx < 0:
                break
            if idx > 0 and cookie[idx - 1] not in "; \t":
                glued = True
                break
            start = idx + len(name)
        if glued:
            warnings.append(f"「{name}」前面少了 `; `，两条 Cookie 粘在一起了，值已被污染")

    return warnings


def _parse_config_text(text: str) -> Tuple[Dict[str, str], List[Dict[str, str]]]:
    """
    解析统一配置文本，返回 (全局配置字典, [账号字典, ...])

    格式（INI 风格，`=` 或 `:` 均可，键名大小写不敏感，支持 # 注释）：

        [mail]                 # 也可以省略这个段头，直接写在最前面
        provider = resend
        api_key = re_xxxxxxxx
        from = GLaDOS_Checkin <onboarding@resend.dev>

        [account]
        name = 主账号
        cookie = koa:sess=xxx; koa:sess.sig=yyy
        mail_to = me@example.com, backup@example.com
        plan = plan500

        [account]              # 再来一段就是一个新账号
        cookie = ...
        mail_to = ...
    """
    global_raw: Dict[str, str] = {}
    accounts: List[Dict[str, str]] = []
    current: Optional[Dict[str, str]] = None

    for raw_line in text.splitlines():
        line = raw_line.replace("\ufeff", "").strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        line = _strip_inline_comment(line)
        if not line:
            continue

        # 段头
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section in ("account", "accounts", "账号", "用户"):
                current = {}
                accounts.append(current)
            elif section in ("mail", "smtp", "resend", "global", "default", "全局", "邮件"):
                current = None  # 回到全局段
            continue

        # key = value 或 key: value
        sep = -1
        for i, ch in enumerate(line):
            if ch in "=:":
                sep = i
                break
        if sep <= 0:
            continue
        key = line[:sep].strip().lower().replace("-", "_")
        value = line[sep + 1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1].strip()
        if not key or not value:
            continue

        target = current if current is not None else global_raw
        # 常见误填：直接把 Cookie 粘进来、忘了写 `cookie = `。那样第一个 `:` 会被当成
        # 键值分隔符（键名变成 `koa`/`gld`），Cookie 整个丢掉，账号被当成没配置。
        if key not in KNOWN_CONFIG_KEYS and any(name in line for name in SESSION_COOKIE_NAMES):
            target["cookie"] = line
            print("提示：检测到一行像是直接粘贴的 Cookie，已按 cookie 处理（建议写成 `cookie = ...`）")
            continue

        target[key] = value

    return global_raw, accounts


def _parse_cookie_lines(text: str) -> List[Dict[str, str]]:
    """
    解析「一堆 Cookie」文本，每行一个账号：
        cookie
        cookie | 备注/名字 | 收件邮箱(逗号分隔) | 兑换计划
    单行时也允许用 && 分隔多个账号。
    """
    if not text or not text.strip():
        return []
    body = text.replace("\r\n", "\n").strip()
    lines = body.split("&&") if ("&&" in body and "\n" not in body) else body.split("\n")

    result = []
    for raw_line in lines:
        line = raw_line.replace("\ufeff", "").strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        line = _strip_inline_comment(line)
        if not line:
            continue
        fields = [f.strip() for f in line.split("|")]
        cookie = fields[0] if fields else ""
        if not cookie:
            continue
        item = {"cookie": cookie}
        if len(fields) > 1 and fields[1]:
            item["name"] = fields[1]
        if len(fields) > 2 and fields[2]:
            item["mail_to"] = fields[2]
        if len(fields) > 3 and fields[3]:
            item["plan"] = fields[3]
        result.append(item)
    return result


def build_mail_settings(global_raw: Dict[str, str], account_raw: Dict[str, str]) -> MailSettings:
    """账号级配置覆盖全局配置，合并成最终发信配置"""

    def pick(*keys: str) -> str:
        return _pick(account_raw, *keys) or _pick(global_raw, *keys)

    settings = MailSettings(
        provider=pick("provider", "mail_provider").lower(),
        api_key=pick("api_key", "resend_api_key", "mail_api_key"),
        sender=pick("from", "mail_from", "sender", "mail_sender"),
        smtp_server=pick("smtp_server", "smtp_host", "server"),
        smtp_port=pick("smtp_port", "port"),
        smtp_user=pick("user", "smtp_user", "mail_user"),
        smtp_pass=pick("pass", "password", "smtp_pass", "mail_pass", "auth_code", "authorization_code"),
    )
    return settings.finalize()


def load_config() -> Tuple[Dict[str, str], List[Account]]:
    """
    读取配置，返回 (全局配置字典, 账号列表)

    优先级：
      1. 环境变量 GLADOS_CONFIG（或 GLADOS_CONFIG_FILE 指向的文件）—— 一个 secret 搞定
      2. 拆分的 secrets：GLADOS_COOKIES / GLADOS_COOKIE + RESEND_API_KEY / SMTP_* + MAIL_TO ...
    两者可以混用：统一配置里只写账号，发信信息用单独的 secret。
    """
    text = os.environ.get("GLADOS_CONFIG", "") or ""
    config_file = os.environ.get("GLADOS_CONFIG_FILE", "")
    if not text.strip() and config_file:
        if not os.path.exists(config_file):
            raise ValueError(f"GLADOS_CONFIG_FILE 指向的文件不存在：{config_file}")
        with open(config_file, "r", encoding="utf-8") as f:
            text = f.read()

    global_raw: Dict[str, str] = {}
    account_raws: List[Dict[str, str]] = []
    if text.strip():
        global_raw, account_raws = _parse_config_text(text)
        print(f"已读取统一配置：全局配置 {len(global_raw)} 项，账号 {len(account_raws)} 个")

    # ---- 发信信息：统一配置里没写的，用拆分的 secrets 兜底 ----
    env_fallback = {
        "provider": os.environ.get("MAIL_PROVIDER", ""),
        "api_key": os.environ.get("RESEND_API_KEY", ""),
        "from": os.environ.get("MAIL_FROM", ""),
        "smtp_server": os.environ.get("SMTP_SERVER", ""),
        "smtp_port": os.environ.get("SMTP_PORT", ""),
        "user": os.environ.get("MAIL_USER", ""),
        "pass": os.environ.get("MAIL_PASS", ""),
    }
    for key, value in env_fallback.items():
        if value and not global_raw.get(key):
            global_raw[key] = value
    # 全局默认收件邮箱与兑换计划
    if os.environ.get("MAIL_TO"):
        global_raw.setdefault("mail_to", os.environ["MAIL_TO"])
    if os.environ.get("GLADOS_EXCHANGE_PLAN"):
        global_raw.setdefault("plan", os.environ["GLADOS_EXCHANGE_PLAN"])

    # ---- User-Agent：GLaDOS 按它判断设备，与登录浏览器不一致会被判为自动化签到 ----
    global USER_AGENT
    override_ua = os.environ.get("GLADOS_USER_AGENT", "") or _pick(global_raw, "user_agent", "ua")
    USER_AGENT = override_ua.strip() or DEFAULT_USER_AGENT

    # ---- 账号信息 ----
    if not account_raws:
        cookie_text = os.environ.get("GLADOS_COOKIES", "") or ""
        raw_accounts: List[Dict[str, str]] = []
        if cookie_text.strip():
            raw_accounts = _parse_cookie_lines(cookie_text)
        elif os.environ.get("GLADOS_COOKIE", "").strip():
            raw_accounts = [{"cookie": os.environ["GLADOS_COOKIE"].strip()}]
        # 统一配置里直接写 cookie / cookies 的简写形式
        if not raw_accounts:
            single = _pick(global_raw, "cookie", "glados_cookie")
            multi = _pick(global_raw, "cookies", "glados_cookies")
            if single:
                raw_accounts = [{"cookie": single}]
            elif multi:
                raw_accounts = [{"cookie": c} for c in _split_cookies(multi)]
        account_raws = raw_accounts

    accounts: List[Account] = []
    for raw in account_raws:
        cookie = _pick(raw, "cookie", "glados_cookie", "token", "cookies")
        if not cookie:
            print(f"跳过一条没有 cookie 的账号配置：{raw}")
            print("  提示：账号段里要写一行 `cookie = koa:sess=...; koa:sess.sig=...`，")
            print("        等号左边是 `cookie`；直接把 Cookie 粘进来会被当成别的键名。")
            continue
        label = _pick(raw, "name", "label", "remark", "note", "备注") or "该账号"
        for warning in check_cookie_shape(cookie):
            print(f"警告（{label}）：{warning}")
        accounts.append(
            Account(
                cookie=cookie,
                name=_pick(raw, "name", "label", "remark", "note", "备注"),
                plan=_pick(raw, "plan", "exchange_plan")
                or _pick(global_raw, "plan", "exchange_plan")
                or DEFAULT_PLAN,
                mail_to=_split_list(
                    _pick(raw, "mail_to", "to", "email", "emails", "mail", "收件邮箱")
                    or _pick(global_raw, "mail_to", "to", "email", "emails")
                ),
                mail_raw=raw,
            )
        )

    return global_raw, accounts


# ---------------------------------------------------------------- 发信部分
def build_email_html(subject, message, account_label="", exchange_msg="", remaining_days="",
                     current_points=0, history=None, plan_info=None):
    """
    构建 HTML 正文（沿用原有邮件格式，仅在拿到账号名时多一行「账号」）
    参数：
        subject: 邮件标题
        message: 简单消息（eg：签到结果）
        account_label: 账号名（单账号且未命名时为空，不会出现在邮件里）
        exchange_msg: 兑换状态信息
        remaining_days: 剩余服务天数
        current_points: 当前总积分
        history: 历史记录列表（由 get_points_history 返回）
        plan_info: 兑换计划信息 (plan_name, required_points, exchange_days)
    """
    html_parts = []
    html_parts.append(f"<h3>{subject}</h3>")
    html_parts.append(f"<p>{message}</p>")
    if account_label:
        html_parts.append(f"<p><b>账号：</b>{account_label}</p>")
    if remaining_days:
        html_parts.append(f"<p><b>剩余服务天数：</b>{remaining_days}</p>")
    html_parts.append(f"<p><b>当前总积分：</b>{current_points}</p>")
    if exchange_msg:
        html_parts.append(f"<p><b>兑换状态：</b>{exchange_msg}</p>")
    if plan_info:
        plan_name, req_pts, days = plan_info
        html_parts.append(f"<p><b>目标兑换计划：</b>{plan_name} (需要 {req_pts} 积分，兑换 {days} 天)</p>")
    if history and len(history) > 0:
        html_parts.append("<h4>近期积分变化记录</h4>")
        html_parts.append('<table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">')
        html_parts.append("<tr><th>日期</th><th>变动值</th><th>余额</th><th>原因</th></tr>")
        for rec in history:
            html_parts.append(f"<tr><td>{rec['date']}</td><td>{rec['change']}</td><td>{rec['balance']}</td><td>{rec['reason']}</td></tr>")
        html_parts.append("</table>")
    else:
        html_parts.append("<p>暂无积分历史记录</p>")
    return "".join(html_parts)


def _mask_email(address: str) -> str:
    """
    邮箱打码，用于日志输出。
    Action 日志在公开仓库里任何人都能看，所以不在日志里放完整收件人地址。
    """
    address = (address or "").strip()
    if "@" not in address:
        return "***" if address else ""
    local, _, domain = address.partition("@")
    keep = local[:2] if len(local) > 2 else local[:1]
    return f"{keep}***@{domain}"


def _mask_recipients(receivers: List[str]) -> str:
    """把收件人列表打码成可直接打日志的文本"""
    return ", ".join(_mask_email(r) for r in receivers)


def send_email_resend(mail: MailSettings, receivers: List[str], subject: str, html_content: str) -> bool:
    """通过 Resend HTTP API 发信"""
    payload = {
        "from": mail.sender,
        "to": receivers,
        "subject": subject,
        "html": html_content,
    }
    try:
        resp = requests.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {mail.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
    except requests.exceptions.RequestException as e:
        print(f"  Resend 发信失败（网络异常）：{e}")
        return False

    if resp.status_code in (200, 201):
        try:
            print(f"  Resend 发信成功：{resp.json().get('id', '')}")
        except ValueError:
            print("  Resend 发信成功")
        return True

    detail = resp.text
    if resp.status_code == 403 and "own email address" in detail:
        detail += "（Resend 未验证域名时只能发给注册 Resend 用的邮箱；想发给别的地址，把 provider 改成 smtp 即可）"
    print(f"  Resend 发信失败 [{resp.status_code}]：{detail[:500]}")
    return False


def send_email_smtp(mail: MailSettings, receivers: List[str], subject: str, html_content: str) -> bool:
    """通过 SMTP 发信（普通邮箱授权码方式）"""
    try:
        msg = MIMEMultipart()
        msg["From"] = Header(mail.sender)
        msg["To"] = Header(", ".join(receivers), "utf-8")
        msg["Subject"] = Header(subject, "utf-8")
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        port = int(mail.smtp_port)
        if port == 465:
            server = smtplib.SMTP_SSL(mail.smtp_server, port, timeout=20)
        else:
            server = smtplib.SMTP(mail.smtp_server, port, timeout=20)
            server.starttls()
        server.login(mail.smtp_user, mail.smtp_pass)
        server.sendmail(mail.smtp_user, receivers, msg.as_string())
        server.quit()
        print(f"  SMTP 发信成功：{_mask_recipients(receivers)}")
        return True
    except Exception as e:
        print(f"  SMTP 发信失败：{e}")
        return False


def send_email(mail: MailSettings, receivers: List[str], subject: str, html_content: str) -> Optional[bool]:
    """
    按配置选择 Resend 或 SMTP 发信。
    返回 True/False；未配置收件人时返回 None（跳过）
    """
    if not receivers:
        print("  未配置收件邮箱，跳过邮件发送")
        return None

    missing = mail.problems()
    if missing:
        print(f"  邮件配置不完整（缺少 {'、'.join(missing)}），跳过邮件发送")
        return False

    print(f"  正在通过 {mail.provider} 向 {_mask_recipients(receivers)} 发送邮件...")
    if mail.provider == "resend":
        return send_email_resend(mail, receivers, subject, html_content)
    return send_email_smtp(mail, receivers, subject, html_content)


# ---------------------------------------------------------------- GLaDOS 接口
# Cookie 失效时 GLaDOS 不会返回 401/403，而是 HTTP 200 + {"code": -2, "message": "没有权限"}。
# 不单独判断的话，日志里只会看到「没有权限」或空的 status 字段，很难判断到底是 Cookie 问题还是站点故障。
AUTH_ERROR_CODES = (-2,)
AUTH_ERROR_MARKERS = ("没有权限", "unauthorized", "not authorized", "forbidden")
AUTH_FAILED_HINT = "Cookie 无效或已过期（GLaDOS 返回「没有权限」），请重新登录 glados.one 后更新 Cookie"
AUTH_FAILED_DAYS = "获取失败（Cookie 无效）"


def _is_auth_error(payload: Dict[str, object]) -> bool:
    """判断响应是不是「Cookie 没通过认证」"""
    try:
        code = int(payload.get("code", 0))
    except (ValueError, TypeError):
        code = 0
    if code in AUTH_ERROR_CODES:
        return True
    message = str(payload.get("message", "")).strip().lower()
    return any(marker in message for marker in AUTH_ERROR_MARKERS)


# 设备不一致时签到接口返回（同样是 HTTP 200）：
# {"code":4,"reason":"device-mismatch","loginDevice":"Windows","currentDevice":"Other",
#  "message":"Automated check-in detected. Please sign in again to continue."}
DEVICE_MISMATCH_CODES = (4,)
DEVICE_MISMATCH_MARKERS = ("automated check-in detected", "device-mismatch", "device mismatch")
DEVICE_MISMATCH_HINT = (
    "GLaDOS 判定为自动化签到（请求设备与登录设备不一致）。"
    "它按 User-Agent 判断设备：请用登录时那个浏览器重新登录一次，"
    "并把 USER_AGENT 换成该浏览器的 UA（环境变量 GLADOS_USER_AGENT，或配置里写 user_agent）"
)


def _is_device_mismatch(payload: Dict[str, object]) -> bool:
    """判断响应是不是「设备与登录设备不一致，被判为自动化」"""
    try:
        code = int(payload.get("code", 0))
    except (ValueError, TypeError):
        code = 0
    if code in DEVICE_MISMATCH_CODES:
        return True
    text = f"{payload.get('reason', '')} {payload.get('message', '')}".lower()
    return any(marker in text for marker in DEVICE_MISMATCH_MARKERS)


def _glados_headers(cookie: str, with_json: bool = False) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": cookie,
        "Referer": "https://glados.one/console/checkin",
    }
    if with_json:
        headers["Content-Type"] = "application/json;charset=UTF-8"
        headers["Origin"] = "https://glados.one"
    return headers


def resolve_plan(plan_name: Optional[str]) -> Tuple[str, int, int]:
    """把计划名解析成 (plan_name, required_points, exchange_days)"""
    name = (plan_name or DEFAULT_PLAN).strip().lower()
    if name not in PLAN_MAP:
        if plan_name:
            print(f"  未知兑换计划 {plan_name}，回退到 {DEFAULT_PLAN}")
        name = DEFAULT_PLAN
    required_points, exchange_days = PLAN_MAP[name]
    return name, required_points, exchange_days


def exchange_points(cookie: str, plan: str) -> Tuple[bool, str]:
    """
    发送 POST 请求到 EXCHANGE_URL，兑换指定计划。
    返回 (success: bool, message: str)
    """
    payload = {"planType": plan}
    try:
        resp = requests.post(
            EXCHANGE_URL,
            json=payload,
            headers=_glados_headers(cookie, with_json=True),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        code = data.get("code", -1)
        if code == 0:
            return True, f"兑换成功：{plan}"
        else:
            msg = data.get("message", "未知错误")
            return False, f"兑换失败：{plan}，错误码 {code}，详情：{msg}"
    except Exception as e:
        return False, f"兑换请求异常：{str(e)}"


def get_points_history(cookie: str, limit: int = 7) -> Tuple[int, List[Dict[str, str]]]:
    """
    获取积分历史记录。
    返回 (current_points, history_list)
    history_list 元素格式：{"date": "2026-04-21", "change": 12, "balance": 142, "reason": "签到"}
    """
    try:
        resp = requests.get(POINTS_URL, headers=_glados_headers(cookie), timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if _is_auth_error(data):
            print(f"  获取积分失败：{AUTH_FAILED_HINT}")
            return 0, []
        current_points_str = data.get("points", "0")
        current_points = int(float(current_points_str))
        history_raw = data.get("history", [])
        # 按时间倒序取最近 limit 条（原始数据一般已按时间倒序，但保险再排一下）
        history_raw.sort(key=lambda x: x.get("time", 0), reverse=True)
        history = []
        for item in history_raw[:limit]:
            change_float = float(item.get("change", "0"))
            balance_float = float(item.get("balance", "0"))
            # 格式化变动值，正数加 + 号
            change_str = f"+{change_float:.0f}" if change_float > 0 else f"{change_float:.0f}"
            reason_map = {
                "system:checkin": "签到",
                "system:exchange": "兑换",
            }
            reason_raw = item.get("business", "")
            reason = reason_map.get(reason_raw, reason_raw)
            history.append({
                "date": item.get("detail", ""),
                "change": change_str,
                "balance": f"{balance_float:.0f}",
                "reason": reason
            })
        return current_points, history
    except Exception as e:
        print(f"  获取积分历史失败: {e}")
        return 0, []


def _short_hash(value: str) -> str:
    """短哈希：用于比对两个账号是否同一个，不泄露原文"""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def _find_identity(data: Dict[str, object]) -> Tuple[str, str]:
    """从 status 返回里找出能标识账号的字段，返回 (字段名, 值)；找不到返回 ("", "")"""
    for key in ("email", "mail", "userEmail", "user_email", "username", "user", "name", "uid", "id"):
        value = data.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            return key, str(value).strip()
    return "", ""


def get_status_info(cookie: str) -> Tuple[str, str]:
    """
    查询账号状态。
    返回 (剩余天数文本, 账号指纹)
    账号指纹 = Cookie 与账号标识字段的短哈希，只用来确认「多个账号填的是不是同一个号」，
    仓库是公开的，所以这里不放 Cookie 或邮箱原文。
    """
    try:
        status_resp = requests.get(STATUS_URL, headers=_glados_headers(cookie), timeout=10)
        if status_resp.status_code == 200:
            status_data = status_resp.json()
            if _is_auth_error(status_data):
                return AUTH_FAILED_DAYS, f"cookie:{_short_hash(cookie)} 账号:认证失败（Cookie 无效或已过期）"
            data = status_data.get("data", {}) or {}
            left_days = data.get("leftDays", "0")
            field, identity = _find_identity(data)
            fingerprint = f"cookie:{_short_hash(cookie)}"
            if field:
                fingerprint += f" 账号({field}):{_short_hash(identity)}"
            else:
                fingerprint += f" 账号:未识别(status 字段: {','.join(sorted(data.keys()))[:60]})"
            return f"{int(float(left_days))} 天", fingerprint
    except Exception as e:
        print(f"  获取剩余天数失败: {e}")
    return "获取失败", f"cookie:{_short_hash(cookie)} 账号:查询失败"


def get_remaining_days(cookie: str) -> str:
    """获取剩余服务天数，失败返回「获取失败」"""
    return get_status_info(cookie)[0]


def do_checkin(cookie: str) -> Tuple[int, bool, str, Optional[int]]:
    """
    执行签到请求。
    返回 (points_gained, success, display_message, current_points)
    - points_gained: int, 本次签到获得的积分（重复签到为 0）
    - success: bool, 是否成功（重复签到也算成功）
    - display_message: str, 给用户看的消息
    - current_points: int, 签到后的当前总积分（失败时为 None）
    """
    payload = {}

    try:
        resp = requests.post(
            CHECKIN_URL,
            json=payload,
            headers=_glados_headers(cookie, with_json=True),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return 0, False, f"网络请求失败: {str(e)}", None
    except ValueError:
        return 0, False, "响应解析失败，非 JSON 格式", None

    # 提取字段
    points_gained_raw = data.get("points", 0)
    message_raw = data.get("message", "")
    list_data = data.get("list", [])

    # Cookie 失效是最常见的问题，单独报出来并给出可操作的提示
    if _is_auth_error(data):
        return 0, False, f"签到失败：{AUTH_FAILED_HINT}", None

    # 新版的设备校验：UA 与登录设备不一致会被判为自动化签到
    if _is_device_mismatch(data):
        return 0, False, f"签到失败：{DEVICE_MISMATCH_HINT}", None

    # 当前总积分（从最新记录中获取 balance）
    current_points = None
    if list_data and len(list_data) > 0:
        try:
            current_points = int(float(list_data[0].get("balance", 0)))
        except (ValueError, TypeError):
            pass

    # 根据业务逻辑判断签到状态
    # 常见情况：
    # - 首次签到成功：points > 0 且 message 包含 "Checkin! Got"
    # - 重复签到：points == 0 且 message 包含 "Checkin Repeats" 或 "logged"
    # - 其他失败：code != 0 或其他异常消息
    try:
        points_value = float(points_gained_raw)
    except (ValueError, TypeError):
        points_value = 0

    if points_value > 0:
        # 签到成功，获得了新积分
        points_gained = int(points_value)
        success = True
        display_message = f"签到成功，获得 {points_gained} 积分"
    elif points_value == 0 and ("logged" in message_raw.lower() or "repeat" in message_raw.lower() or "tomorrow" in message_raw.lower()):
        # 今日已签到过
        points_gained = 0
        success = True
        display_message = "今日已签到，明天再来吧"
    else:
        # 签到失败（可能是 Cookie 失效、服务器错误等）
        points_gained = 0
        success = False
        display_message = f"签到失败：{message_raw if message_raw else '未知错误'}"

    # 如果成功但 current_points 仍为 None，尝试从 points 接口获取（降级）
    if success and current_points is None:
        try:
            points_resp = requests.get(POINTS_URL, headers=_glados_headers(cookie), timeout=10)
            if points_resp.status_code == 200:
                points_data = points_resp.json()
                current_points = int(float(points_data.get("points", 0)))
        except Exception:
            pass

    return points_gained, success, display_message, current_points


# ---------------------------------------------------------------- 单账号流程
def run_account(account: Account, global_raw: Dict[str, str], label: str) -> Dict[str, object]:
    """处理一个账号：签到 -> 兑换 -> 发信，返回结果字典"""
    points_gained, success, checkin_msg, _ = do_checkin(account.cookie)

    remaining_days_str, identity_fingerprint = get_status_info(account.cookie)
    print(f"  账号指纹: {identity_fingerprint}")
    current_points, history = get_points_history(account.cookie, 7)

    plan_name, required_points, exchange_days = resolve_plan(account.plan)
    plan_info = (plan_name, required_points, exchange_days)

    exchange_msg = ""
    if current_points >= required_points:
        exchange_success, exchange_msg = exchange_points(account.cookie, plan_name)
        if exchange_success:
            # 兑换成功，重新获取积分历史（会包含扣除记录）
            current_points, history = get_points_history(account.cookie, 7)
            remaining_days_str = get_remaining_days(account.cookie)
    else:
        need = required_points - current_points
        exchange_msg = f"积分不足，距离兑换 {plan_name} 还差 {need} 积分"

    # 构造邮件主题和正文
    if success:
        subject = "✅ GLaDOS 签到成功"
        body_msg = f"{checkin_msg}，本次获得 {points_gained} 积分"
    else:
        subject = "❌ GLaDOS 签到失败"
        body_msg = checkin_msg

    html_content = build_email_html(
        subject=subject,
        message=body_msg,
        account_label=label,
        exchange_msg=exchange_msg,
        remaining_days=remaining_days_str,
        current_points=current_points,
        history=history,
        plan_info=plan_info,
    )

    mail_settings = build_mail_settings(global_raw, account.mail_raw)
    mail_sent = send_email(mail_settings, account.mail_to, subject, html_content)

    print(f"  {datetime.now()} - {subject} - {body_msg}")

    return {
        "label": label or "账号",
        "success": success,
        "message": body_msg,
        "points_gained": points_gained,
        "current_points": current_points,
        "remaining_days": remaining_days_str,
        "exchange_msg": exchange_msg,
        "mail_sent": mail_sent,
    }


def write_step_summary(results: List[Dict[str, object]]) -> None:
    """把多账号结果写进 GitHub Actions 的 Summary 面板（本地运行自动跳过）"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = [
        "## GLaDOS 签到结果",
        "",
        "| 账号 | 结果 | 当前积分 | 剩余天数 | 兑换状态 | 邮件 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    mail_text = {True: "已发送", False: "发送失败", None: "未配置"}
    for r in results:
        lines.append(
            "| {label} | {state} | {points} | {days} | {exchange} | {mail} |".format(
                label=r["label"],
                state=("✅ " if r["success"] else "❌ ") + str(r["message"]),
                points=r["current_points"],
                days=r["remaining_days"],
                exchange=str(r["exchange_msg"]).replace("|", "/") or "-",
                mail=mail_text.get(r["mail_sent"], "-"),
            )
        )
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def check_cookie_cli(cookie: str) -> int:
    """
    本地校验 Cookie 是否有效：只读，不会签到、不消耗当天签到次数。
    改完 Cookie 先本地确认能通过认证，再写进 GitHub secret，省得等 Actions 跑完才知道。
    """
    cookie = (cookie or "").strip()
    if not cookie:
        print("用法：")
        print('  python GLaDOS_Checkin.py --check-cookie "koa:sess=...; koa:sess.sig=..."')
        print('  echo "koa:sess=...; koa:sess.sig=..." | python GLaDOS_Checkin.py --check-cookie')
        return 2

    print("== 仅校验 Cookie（只读，不会执行签到）==")

    has_session = f"{SESSION_COOKIE_NAME}=" in cookie
    print(
        f"   会话 Cookie {SESSION_COOKIE_NAME}：{'有 ✅' if has_session else '没有 ❌'}"
        + (f"（另外带了 {LEGACY_COOKIE_NAME}，它已不用于认证）" if f"{LEGACY_COOKIE_NAME}=" in cookie else "")
    )

    shape_warnings = check_cookie_shape(cookie)
    if shape_warnings:
        print("⚠️  Cookie 本身就有问题，先修这些：")
        for warning in shape_warnings:
            print(f"   - {warning}")
        print("   复制方法：F12 → Network → 点一个 glados.one 的 api/user/... 请求 →")
        print("   把「请求头」里 Cookie 那一行完整复制（网络面板里的值一定是完整、带分隔符的）。")
    else:
        print("   Cookie 格式看起来正常（gld:sess 成对、没有粘连）")

    try:
        resp = requests.get(STATUS_URL, headers=_glados_headers(cookie), timeout=15)
    except requests.exceptions.RequestException as e:
        print(f"❌ 网络请求失败：{e}")
        return 1

    if resp.status_code != 200:
        print(f"❌ 请求失败：HTTP {resp.status_code}")
        return 1

    try:
        payload = resp.json()
    except ValueError:
        print(f"❌ 返回内容不是 JSON：{resp.text[:200]}")
        return 1

    if _is_auth_error(payload):
        print(f"❌ Cookie 无效：GLaDOS 返回 {payload}")
        print(f"   {AUTH_FAILED_HINT}")
        if shape_warnings:
            print("   ↑ 上面的格式问题基本就是原因，先按那个重新复制一次。")
        print("   按顺序排查：")
        print("     1. Cookie 是不是两次复制混在一起的（同一个名字出现两次、值还不一样）")
        print("     2. 复制时是不是漏了 `; `，把两条 Cookie 粘成了一行")
        print("     3. 复制之后浏览器是不是又登录过/退出过，把那个会话作废了")
        print("     4. 域名对不对：glados.one 才是本脚本用的站点")
        return 1

    data = payload.get("data", {}) or {}
    field, identity = _find_identity(data)
    # 接口给的是浮点字符串（如 557.0000000000000000），这里跟邮件里的显示保持一致
    left_days_raw = data.get("leftDays", "?")
    try:
        left_days = f"{int(float(left_days_raw))} 天"
    except (ValueError, TypeError):
        left_days = str(left_days_raw)
    print("✅ Cookie 有效")
    print(f"   登录账号：{identity if field else '（接口未返回账号标识）'}")
    print(f"   剩余服务天数：{left_days}")

    current_points, history = get_points_history(cookie, 3)
    print(f"   当前总积分：{current_points}")
    if history:
        print("   最近积分变动：")
        for rec in history:
            print(f"     {rec['date']}  {rec['change']}  ->  {rec['balance']}  ({rec['reason']})")
    print("\n这个 Cookie 可以直接写进 GitHub secret 了。")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in ("--check-cookie", "-c", "--check"):
        cookie = args[1] if len(args) > 1 else sys.stdin.read()
        return check_cookie_cli(cookie)

    print(f"=== GLaDOS 签到开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")

    try:
        global_raw, accounts = load_config()
    except Exception as e:
        print(f"配置解析失败：{e}")
        return 1

    if not accounts:
        print("未找到任何账号配置。请检查 GLADOS_CONFIG / GLADOS_COOKIES / GLADOS_COOKIE 是否已设置。")
        return 1

    total = len(accounts)
    print(f"共 {total} 个账号待签到")
    print(f"请求 User-Agent: {USER_AGENT}")
    print("（GLaDOS 按它比对登录设备；若报「Automated check-in detected」就把它换成你登录浏览器的 UA）\n")

    results: List[Dict[str, object]] = []
    for index, account in enumerate(accounts, 1):
        label = account.label(index, total)
        print(f"----- [{index}/{total}] {label or '账号'} -----")
        try:
            results.append(run_account(account, global_raw, label))
        except Exception as e:
            print(f"  账号处理异常：{e}")
            results.append({
                "label": label or f"账号{index}",
                "success": False,
                "message": f"账号处理异常：{e}",
                "points_gained": 0,
                "current_points": "-",
                "remaining_days": "-",
                "exchange_msg": "",
                "mail_sent": False,
            })
        print("")

    write_step_summary(results)

    # 汇总
    print("=== 汇总 ===")
    failed = []
    for r in results:
        state = "成功" if r["success"] else "失败"
        print(f"  {r['label']}：{state} - {r['message']}")
        if not r["success"] or r["mail_sent"] is False:
            failed.append(str(r["label"]))

    if failed:
        print(f"\n以下账号签到或发信未成功：{'、'.join(failed)}")
        return 1

    print("\n全部账号处理完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
