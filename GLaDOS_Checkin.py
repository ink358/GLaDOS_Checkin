#用于GitHub actions 
import os
import smtplib
import requests
import json
from datetime import datetime
# from winotify import Notification, audio

# ========== 配置区 ==========
#   PAYLOAD = {}                         # 如果是空对象
#   PAYLOAD = {"token": "some_token"}    # 如果包含 token
#   PAYLOAD = '{"operation":"checkin"}'  # 如果是字符串
PAYLOAD = {}   

# 从环境变量读取 Cookie（必须）
COOKIE_STR = os.environ.get("GLADOS_COOKIE")
if not COOKIE_STR:
    raise ValueError("未设置 GLADOS_COOKIE 环境变量")

# 兑换计划（可选，默认 plan500）
os.environ["GLADOS_EXCHANGE_PLAN"] = os.environ.get("GLADOS_EXCHANGE_PLAN", "plan500")



# API 地址
CHECKIN_URL = "https://glados.one/api/user/checkin"
STATUS_URL = "https://glados.one/api/user/status"
POINTS_URL = "https://glados.one/api/user/points"
EXCHANGE_URL = "https://glados.one/api/user/exchange"
# ==========================

def get_exchange_plan():
    """
    从环境变量 GLADOS_EXCHANGE_PLAN 读取兑换计划。
    返回 (plan_name, required_points, exchange_days)
    """
    plan_map = {
        "plan100": (100, 10),
        "plan200": (200, 30),
        "plan500": (500, 100)
    }
    env_plan = os.environ.get("GLADOS_EXCHANGE_PLAN", "plan500").lower()
    if env_plan not in plan_map:
        env_plan = "plan500"
    required_points, exchange_days = plan_map[env_plan]
    return env_plan, required_points, exchange_days

def exchange_points(plan):
    """
    发送 POST 请求到 EXCHANGE_URL，兑换指定计划。
    返回 (success: bool, message: str)
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://glados.one",
        "Referer": "https://glados.one/console/checkin",
        "Cookie": COOKIE_STR,
    }
    payload = {"planType": plan}
    try:
        resp = requests.post(EXCHANGE_URL, json=payload, headers=headers, timeout=10)
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

def get_points_history(limit=7):
    """
    获取积分历史记录。
    返回 (current_points, history_list)
    history_list 元素格式：{"date": "2026-04-21", "change": 12, "balance": 142, "reason": "签到"}
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "Cookie": COOKIE_STR,
        "Referer": "https://glados.one/console/checkin",
    }
    try:
        resp = requests.get(POINTS_URL, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
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
        print(f"获取积分历史失败: {e}")
        return 0, []

def send_email(subject, message, exchange_msg="", remaining_days="", current_points=0, history=None, plan_info=None):
    """
    发送邮件，支持附加积分历史表格和兑换信息。
    参数：
        subject: 邮件标题
        message: 简单消息（eg：签到结果）
        exchange_msg: 兑换状态信息
        remaining_days: 剩余服务天数
        current_points: 当前总积分
        history: 历史记录列表（由 get_points_history 返回）
        plan_info: 兑换计划信息 (plan_name, required_points, exchange_days)
    """
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    from email.header import Header

    # 从环境变量读取邮箱配置（建议使用 Secrets）
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.qq.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    sender_email = os.environ.get("MAIL_USER")
    sender_password = os.environ.get("MAIL_PASS")
    receiver_email = os.environ.get("MAIL_TO")

    # # 如果是在本地测试，可以在这里设置一个备用值，方便调试
    # if not sender_email:
    #     sender_email = '' # 你的本地测试邮箱
    # if not sender_password:
    #     sender_password = '' # 你的本地测试授权码
    # if not receiver_email:
    #     receiver_email = '' # 你的本地测试收件邮箱

    if not all([sender_email, sender_password, receiver_email]):
        print("邮件配置不完整，跳过发送")
        return

    # 构建 HTML 正文
    html_parts = []
    html_parts.append(f"<h3>{subject}</h3>")
    html_parts.append(f"<p>{message}</p>")
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
    html_content = "".join(html_parts)
    
    try:
        msg = MIMEMultipart()
        msg['From'] = Header(f"GLaDOS_Checkin <{sender_email}>")
        msg['To'] = Header(f" <{receiver_email}>")
        msg['Subject'] = Header(subject, 'utf-8')
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        # 根据端口选择 SSL 或 TLS
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)
        else:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, [receiver_email], msg.as_string())
        server.quit()
        print("邮件发送成功")
    except Exception as e:
        print(f"邮件发送失败: {e}")

# def send_notification(title, message, success=True):
#     """发送 Windows 通知"""
#     notif = Notification(
#         app_id="GLaDOS签到",
#         title=title,
#         msg=message,
#         duration="short"
#     )
#     notif.set_audio(audio.Default, loop=False)
#     notif.show()
 
def do_checkin():
    """
    执行签到请求。
    返回 (points_gained, success, display_message, current_points)
    - points_gained: int, 本次签到获得的积分（重复签到为 0）
    - success: bool, 是否成功（重复签到也算成功）
    - display_message: str, 给用户看的消息
    - current_points: int, 签到后的当前总积分（失败时为 None）
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://glados.one",
        "Referer": "https://glados.one/console/checkin",
        "Cookie": COOKIE_STR,
    }
    # 根据你之前抓包的 payload，可能需要携带参数；如果没有特殊参数，传空对象即可
    payload = {}  

    try:
        resp = requests.post(CHECKIN_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.RequestException as e:
        return 0, False, f"网络请求失败: {str(e)}", None
    except json.JSONDecodeError:
        return 0, False, "响应解析失败，非 JSON 格式", None

    # 提取字段
    code = data.get("code", -1)
    points_gained_raw = data.get("points", 0)
    message_raw = data.get("message", "")
    list_data = data.get("list", [])

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
    if points_gained_raw > 0:
        # 签到成功，获得了新积分
        points_gained = int(float(points_gained_raw))
        success = True
        display_message = f"签到成功，获得 {points_gained} 积分"
    elif points_gained_raw == 0 and ("logged" in message_raw.lower() or "repeat" in message_raw.lower() or "tomorrow" in message_raw.lower()):
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
            points_resp = requests.get(POINTS_URL, headers={"Cookie": COOKIE_STR}, timeout=10)
            if points_resp.status_code == 200:
                points_data = points_resp.json()
                current_points = int(float(points_data.get("points", 0)))
        except:
            pass

    return points_gained, success, display_message, current_points

def main():
    # 1. 签到
    points_gained, success, checkin_msg, current_points = do_checkin()  
    # 获取剩余天数
    remaining_days_str = "获取失败"
    try:
        status_resp = requests.get(STATUS_URL, headers={"Cookie": COOKIE_STR}, timeout=10)
        if status_resp.status_code == 200:
            status_data = status_resp.json()
            left_days = status_data.get("data", {}).get("leftDays", "0")
            remaining_days_str = f"{int(float(left_days))} 天"
    except Exception as e:
        print(f"获取剩余天数失败: {e}")
    
    # 获取当前积分和最近7天历史
    current_points, history = get_points_history(7)
    
    # 获取兑换计划
    plan_name, required_points, exchange_days = get_exchange_plan()
    plan_info = (plan_name, required_points, exchange_days)
    
    exchange_msg = ""
    if current_points >= required_points:
        # 执行兑换
        exchange_success, exchange_msg = exchange_points(plan_name)
        if exchange_success:
            # 兑换成功，重新获取积分历史（会包含扣除记录）
            current_points, history = get_points_history(7)
            # 可选：再次获取剩余天数更新
            try:
                status_resp = requests.get(STATUS_URL, headers={"Cookie": COOKIE_STR}, timeout=10)
                if status_resp.status_code == 200:
                    left_days = status_resp.json().get("data", {}).get("leftDays", "0")
                    remaining_days_str = f"{int(float(left_days))} 天"
            except:
                pass
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
    
    # 发送邮件
    send_email(
        subject=subject,
        message=body_msg,
        exchange_msg=exchange_msg,
        remaining_days=remaining_days_str,
        current_points=current_points,
        history=history,
        plan_info=plan_info
    )
    
    # Windows 通知（可选，保留原有）
    # send_notification(subject, body_msg, success)
    
    print(f"{datetime.now()} - {subject} - {body_msg}")
    import sys
    sys.exit(0)

if __name__ == "__main__":
    main()
