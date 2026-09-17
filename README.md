# GLaDOS 自动签到

每天自动给一个或多个 GLaDOS 账号签到、自动兑换流量天数，并把**每个账号的签到结果分别发邮件**给你。

- ✅ 多账号：一个 Cookie 一行，账号数量不限
- ✅ 每个账号一封独立邮件，收件人可以按账号分别指定（也可以一个账号发给多个邮箱）
- ✅ 发信支持 **Resend API**（推荐，代码干净）和 **SMTP**（QQ / 163 / Gmail 等普通邮箱授权码）
- ✅ 邮件正文沿用原格式：签到结果 + 剩余天数 + 当前积分 + 兑换状态 + 目标兑换计划 + 近 7 天积分变化表
- ✅ 敏感信息只放 GitHub Secrets，仓库里不出现任何 Cookie / API Key
- ✅ 配置可以「一个 secret 搞定」，也可以拆成一组 secrets，怎么顺手怎么来

---

## 一、快速开始

1. Fork / 克隆本仓库。
2. 准备好每个账号的 GLaDOS Cookie（见下方「怎么拿 Cookie」）。
3. 去 [resend.com](https://resend.com/) 注册并拿到 API Key（见下方「怎么拿 Resend API Key」），或者准备一个普通邮箱的 SMTP 授权码。
4. 到仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加 secret（见下方配置说明）。
5. 到 **Actions → GLaDOS Daily Checkin → Run workflow** 手动跑一次，看日志确认成功。

### 怎么拿 Cookie

浏览器登录 `https://glados.one`，F12 → Network → 随便点一个 `api/user/...` 请求 → 复制请求头里的 `Cookie` 整行（形如 `koa:sess=xxxx; koa:sess.sig=yyyy`）。

> Cookie 等于账号密码，不要提交到仓库、不要发给别人。过期了就重新抓一次，更新 secret 即可。

### 怎么拿 Resend API Key

1. 打开 <https://resend.com/>，用 **GitHub 账号**登录（Sign up with GitHub）。
2. 左侧菜单 **API Keys → Create API Key**，权限选 `Sending access` 即可，创建后复制 `re_` 开头的 Key（只显示一次）。
3. 脚本只要发一个 HTTP 请求就能发信：

```python
import requests

resp = requests.post(
    "https://api.resend.com/emails",
    headers={"Authorization": "Bearer re_你的APIKey"},
    json={
        "from": "签到提醒 <onboarding@resend.dev>",
        "to": ["你的邮箱@example.com"],
        "subject": "签到成功",
        "text": "今日签到已完成 ✅"
    }
)
print(resp.json())  # {"id": "em_xxxx"}
```

本项目发的是原格式的 HTML 正文，所以用的是 `html` 字段，其余同上。

> ⚠️ **Resend 的免费测试发件人限制**：没验证自己的域名时，只能用 `onboarding@resend.dev` 发信，而且**只能发给你注册 Resend 用的那个邮箱**，发给别的地址会报 `403 domain is not verified`。
> - 只发给自己 → 直接用默认发件人即可，什么都不用改。
> - 要发给别人（或想用自己的域名） → 在 Resend 里 **Domains → Add Domain** 验证域名，然后把发件人改成 `你的名字 <noreply@你的域名>`。

---

## 二、配置方式（二选一，也可以混用）

### 方式一：一个 secret 搞定（推荐）

只加 **一个** repository secret：`GLADOS_CONFIG`，值就是下面这段文本（GitHub secret 支持多行）。

```ini
# ===== 发信配置（全局）=====
[mail]
provider = resend
api_key = re_你的APIKey
from = GLaDOS_Checkin <onboarding@resend.dev>

# ===== 账号 1 =====
[account]
name = 主账号
cookie = koa:sess=aaaa; koa:sess.sig=bbbb
mail_to = me@example.com, backup@example.com
plan = plan500

# ===== 账号 2 =====
[account]
name = 小号
cookie = koa:sess=cccc; koa:sess.sig=dddd
mail_to = other@example.com
plan = plan200
```

规则：

- `[account]` 出现几次就是几个账号，每个账号会收到**自己那封**邮件。
- `[mail]` 段写发信配置；也可以省略段头，把发信配置直接写在最前面。
- 键名不区分大小写，`=` 和 `:` 都行，`#` 开头是注释，行尾 ` # 注释` 也可以。
- 账号里的 `mail_to` / `plan` / 发信字段会**覆盖**全局配置；没写的就继承全局。
- 只有一个账号时，可以直接写 `cookie = xxx`，不用 `[account]` 段；多个账号用 `cookies = cookie1 && cookie2`。

#### 可用字段

| 字段 | 位置 | 说明 |
| --- | --- | --- |
| `provider` | 全局 / 账号 | `resend` 或 `smtp`。两种凭据都配了就必须显式写，否则按 smtp 优先 |
| `api_key` | 全局 / 账号 | Resend API Key（别名 `resend_api_key`） |
| `from` | 全局 / 账号 | 发件人，可写 `名字 <地址>` 或裸地址（别名 `mail_from`） |
| `smtp_server` / `smtp_port` | 全局 / 账号 | SMTP 服务器与端口，默认 `smtp.qq.com:465`（465 走 SSL，其他端口走 STARTTLS） |
| `user` / `pass` | 全局 / 账号 | SMTP 账号与授权码（别名 `mail_user` / `mail_pass`） |
| `name` | 账号 | 账号备注，会出现在邮件正文里，便于区分是哪个号 |
| `cookie` | 账号 | GLaDOS Cookie（必填） |
| `mail_to` | 全局 / 账号 | 收件人，多个用逗号（或分号、空格）分隔 |
| `plan` | 全局 / 账号 | 兑换计划：`plan100` / `plan200` / `plan500`，默认 `plan500` |

用普通邮箱（SMTP）时，`[mail]` 段换成：

```ini
[mail]
provider = smtp
smtp_server = smtp.qq.com
smtp_port = 465
user = 你的QQ号@qq.com
pass = 邮箱授权码      # 不是登录密码，在邮箱设置里开启 SMTP 后生成
from = GLaDOS_Checkin <你的QQ号@qq.com>
```

也支持本地文件方式（方便调试）：把同样的文本存成文件，然后设环境变量 `GLADOS_CONFIG_FILE=路径`。

### 方式二：拆分的 secrets

不想把 Cookie 和邮件配置放在一个 secret 里，就分开加：

| Secret 名 | 必填 | 说明 |
| --- | --- | --- |
| `GLADOS_COOKIES` | 二选一 | 多个账号，**一行一个 Cookie**；也可以在行尾用 `\|` 追加信息：`cookie \| 备注 \| 收件邮箱 \| 计划` |
| `GLADOS_COOKIE` | 二选一 | 只有一个账号时用它（老配置继续可用） |
| `GLADOS_EXCHANGE_PLAN` | 否 | 全局兑换计划，默认 `plan500` |
| `MAIL_PROVIDER` | 否 | `resend` / `smtp`，一般不用写，脚本会自动判断 |
| `RESEND_API_KEY` | 用 Resend 时 | Resend API Key |
| `MAIL_FROM` | 否 | 发件人，不写则用 `GLaDOS_Checkin <onboarding@resend.dev>`（SMTP 时默认用 `MAIL_USER`） |
| `SMTP_SERVER` / `SMTP_PORT` | 用 SMTP 时 | 默认 `smtp.qq.com` / `465` |
| `MAIL_USER` / `MAIL_PASS` | 用 SMTP 时 | 邮箱账号 / 授权码 |
| `MAIL_TO` | 否 | 收件人，多个用逗号分隔。**所有账号**的结果都会分别发到这些地址 |

`GLADOS_COOKIES` 的示例（3 个账号，第 3 个用全局收件人）：

```
koa:sess=a1; koa:sess.sig=b1 | 主账号 | me@example.com, backup@example.com | plan500
koa:sess=a2; koa:sess.sig=b2 | 小号 | other@example.com
koa:sess=a3; koa:sess.sig=b3
```

混用也没问题：比如 `GLADOS_CONFIG` 里只写 `[account]`，API Key 单独放 `RESEND_API_KEY`。

---

## 三、邮件长什么样

正文格式与原项目保持一致（签到结果、剩余服务天数、当前总积分、兑换状态、目标兑换计划、近 7 天积分变化表）。

区别只有一处：配置了账号名（`name`）或存在多个账号时，正文里会多一行 `账号：xxx`，用来区分这封邮件是哪个号的。单账号且没写 `name` 时，邮件内容与原来完全一致。

每个账号单独一封邮件，互不影响；某个账号失败也会继续处理下一个。

---

## 四、兑换计划

`plan100` = 100 积分兑 10 天，`plan200` = 200 积分兑 30 天，`plan500` = 500 积分兑 100 天。
积分够当前计划就自动兑换，不够则在邮件里提示还差多少。

---

## 五、本地运行 / 调试

```bash
pip install requests

# 把配置文本存成 config.local.txt 后：
GLADOS_CONFIG_FILE=config.local.txt python GLaDOS_Checkin.py
```

Linux/macOS 上也可以临时导出环境变量跑：

```bash
GLADOS_COOKIE='koa:sess=...' RESEND_API_KEY='re_...' MAIL_TO='me@example.com' python GLaDOS_Checkin.py
```

> 本地调试用的配置文件不要提交（`.gitignore` 已忽略 `config.local.*`）。

---

## 六、常见问题

**Q：Action 变红了怎么办？**
多账号场景下，只要有一个账号签到失败或邮件发送失败，脚本就以退出码 1 结束，方便你收到 GitHub 的失败通知。日志里会列出具体是哪些账号有问题，Actions 页面的 Summary 还有一张结果表。

**Q：邮件里写「今日已签到，明天再来吧」，是失败吗？**
不是。重复签到算成功，只是没有新积分。

**Q：提示 `403 domain is not verified`？**
Resend 没验证域名时只能用 `onboarding@resend.dev` 发给注册 Resend 用的邮箱。要么把收件人改成那个邮箱，要么去 Resend 验证自己的域名并修改 `from`。

**Q：提示 Cookie 不含 `koa:sess`？**
Cookie 复制不完整或已失效，重新抓一次完整 Cookie 并更新 secret。

**Q：签名/发信失败会让签到白跑吗？**
签到本身不受影响，账号该签的都签了，只是邮件没发出去。
