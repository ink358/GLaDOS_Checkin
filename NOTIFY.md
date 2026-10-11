# 通知方式

GLaDOS 签到脚本支持 **4 种通知渠道**，可以同时开启多个：

| 渠道 | 费用 | 收到消息的地方 | 需要准备的东西 |
| --- | --- | --- | --- |
| 邮件（SMTP） | 免费 | 任意邮箱 | 一个邮箱的 SMTP 授权码 |
| 邮件（Resend） | 免费额度 | 任意邮箱（需自有域名） | Resend API Key |
| Telegram | 免费 | Telegram 聊天 | 一个 Bot Token + Chat ID |
| Server酱 | 免费额度 | 微信 | 一个 SendKey |
| PushPlus | 免费额度 | 微信 | 一个 Token |

通用规则：

- **每个账号分别发送**：有 2 个账号，邮件就是 2 封，Telegram / Server酱 / PushPlus 也是各推 2 条。
- **渠道凭据有两种写法**：写进 `GLADOS_CONFIG` 的对应段里（见下方示例），或者用独立的 repository secret（见文末速查表）。
- **互不影响**：某个渠道发送失败不影响其他渠道；只有「签到失败」或「已配置的渠道发送失败」才会让 Action 变红（退出码 1）。
- **日志会打码**：公开仓库的 Actions 日志任何人都能看，所以收件邮箱会显示成 `in***@qq.com`，凭据一律不打印。
- **只想要一种**：邮件最省事，直接跳到「1. 邮件（SMTP）」。不用邮件、只想微信收消息就选 Server酱 或 PushPlus。

`GLADOS_CONFIG` 里同时开启多个渠道长这样（按需保留对应段）：

```ini
[mail]
provider = smtp
smtp_server = smtp.qq.com
smtp_port = 465
user = 你的QQ号@qq.com
pass = 邮箱授权码
mail_to = me@example.com, backup@example.com

[telegram]
bot_token = 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxx
chat_id = 1008610086

[serverchan]
sendkey = SCT123456XXXXXXXXXXXXXXXXXXXXXXXXXXXX

[pushplus]
token = 你的pushplus_token
```

> 键名不区分大小写，`=` 和 `:` 都行，`#` 开头是注释。
> 这些渠道凭据也可以单独写在独立 secret 里，此时 `GLADOS_CONFIG` 只需写账号段即可。

---

## 1. 邮件（SMTP，推荐）

普通邮箱都能用，收件人不受限制，一个账号想发给几个人就逗号隔开写几个。

**拿授权码**（注意：授权码不是邮箱登录密码）：

| 邮箱 | 服务器 | 端口 | 授权码在哪生成 |
| --- | --- | --- | --- |
| QQ 邮箱 | `smtp.qq.com` | 465 | 设置 → 账户 → 开启「SMTP 服务」→ 生成授权码 |
| 163 邮箱 | `smtp.163.com` | 465 | 设置 → POP3/SMTP/IMAP → 开启服务 → 新增授权码 |
| Gmail | `smtp.gmail.com` | 587 | 先开启两步验证，再到「应用专用密码」生成 |
| Outlook / Office365 | `smtp.office365.com` | 587 | 账户安全 → 高级安全选项 → 应用密码 |

> 端口 465 走 SSL，其他端口（587 等）走 STARTTLS，脚本自动判断。

**配置**：

```ini
[mail]
provider = smtp
smtp_server = smtp.qq.com
smtp_port = 465
user = 你的QQ号@qq.com
pass = 授权码
from = GLaDOS_Checkin <你的QQ号@qq.com>   # 可不写，不写就用 user
mail_to = me@example.com                 # 收件人，多个用逗号分隔
```

| 字段 | 说明 |
| --- | --- |
| `smtp_server` / `smtp_port` | 默认 `smtp.qq.com` / `465`（别名 `smtp_host` / `server` / `port`） |
| `user` / `pass` | SMTP 账号与授权码（别名 `mail_user` / `mail_pass` / `auth_code`） |
| `from` | 发件人，可写 `名字 <地址>` 或裸地址；不写则用 `user` |
| `mail_to` | 收件人，多个用逗号 / 分号 / 空格分隔 |

`mail_to` 既能写在全局（所有账号都发这些地址），也能写在某个 `[account]` 段里（只给这个账号用），账号段会覆盖全局。

独立 secret 写法：`SMTP_SERVER` / `SMTP_PORT` / `MAIL_USER` / `MAIL_PASS` / `MAIL_FROM` / `MAIL_TO`。

---

## 2. 邮件（Resend，可选，需要自有域名）

[Resend](https://resend.com/) 的 API 很干净，但**没有自己域名的人不建议用**：

- 没验证域名时只能用 `onboarding@resend.dev` 发信，而且**只能发给你注册 Resend 用的那个邮箱**。发给其他地址会整封失败，报 `403 You can only send testing emails to your own email address`。
- 想发给别人，必须先 **Domains → Add Domain** 验证一个你自己的域名，再把 `from` 改成 `你的名字 <noreply@你的域名>`。

要用的话：用 GitHub 账号登录 <https://resend.com/> → **API Keys → Create API Key**（权限选 `Sending access`）→ 复制 `re_` 开头的 Key（只显示一次）。

```ini
[mail]
provider = resend
api_key = re_你的APIKey
from = GLaDOS_Checkin <noreply@你的域名>
mail_to = me@example.com
```

独立 secret 写法：`MAIL_PROVIDER=resend` / `RESEND_API_KEY` / `MAIL_FROM` / `MAIL_TO`。

> 从 Resend 换回 SMTP 时，记得删掉 `from` 里的 `onboarding@resend.dev`，否则 SMTP 发信会报错。

---

## 3. Telegram

1. 在 Telegram 里找 [@BotFather](https://t.me/BotFather)，发 `/newbot`，按提示起名，拿到 **Bot Token**（形如 `123456789:AA....`）。
2. 先给自己的 bot 发一条消息（随便发个 `hi`），否则 bot 不能主动给你发消息。
3. 找 **Chat ID**：给 [@userinfobot](https://t.me/userinfobot) 发消息，它会回你的数字 ID；或把 bot 拉进群里后访问
   `https://api.telegram.org/bot<你的Token>/getUpdates` 查看 `chat.id`。

配置（`[telegram]` 段里的键会自动加前缀，等价于全局写 `telegram_bot_token` / `telegram_chat_id`）：

```ini
[telegram]
bot_token = 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxx
chat_id = 1008610086
```

独立 secret 写法：`TG_BOT_TOKEN` / `TG_CHAT_ID`（也认 `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`）。

> 脚本按纯文本发送，不会触发 Telegram 的 Markdown 解析错误。
> 中国大陆访问 `api.telegram.org` 可能需要代理，Actions 的海外机器没有这个问题。

---

## 4. Server酱（微信）

1. 打开 <https://sct.ftqq.com/>，用微信扫码登录。
2. 按页面引导绑定微信 / 关注公众号，在 **SendKey** 页面复制你的 SendKey。

配置：

```ini
[serverchan]
sendkey = SCT123456XXXXXXXXXXXXXXXXXXXXXXXXXXXX
```

独立 secret 写法：`SCT_SENDKEY`（也认 `SERVERCHAN_SENDKEY`）。

> 免费版有每日推送条数限制；消息会发到微信公众号「Server酱」。

---

## 5. PushPlus（微信）

1. 打开 <https://www.pushplus.plus/>，用微信扫码登录。
2. 在「一对一推送」页面复制你的 **token**（32 位字符串）。

配置：

```ini
[pushplus]
token = 你的pushplus_token
```

独立 secret 写法：`PUSHPLUS_TOKEN`。

> 免费版有每日条数限制；消息会发到微信公众号「pushplus 推送加」。

---

## 6. 测试通知：`TEST` secret

`TEST` 这个 repository secret 的**值本身就是一份通知配置**（格式和 `GLADOS_CONFIG` 一样）。
只要它存在，脚本就进入**通知测试模式**：

- **不会执行任何签到 / 兑换**，完全不访问 GLaDOS，也不需要账号 Cookie；
- 把 `TEST` 里写的渠道各发一条测试消息；
- 日志里逐个渠道打印成功 / 失败，方便确认凭据对不对。

所以你可以把**还没敢填进正式配置**的新凭据先写进 `TEST` 试一下：能收到测试消息，再把它挪进 `GLADOS_CONFIG`。

`TEST` 的值示例（只测 Telegram，就只写 Telegram 段）：

```ini
[telegram]
bot_token = 123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxx
chat_id = 1008610086
```

也可以同时写多个渠道；邮件用 `[mail]` 段（`mail_to` 就是测试邮件的收件人）：

```ini
[pushplus]
token = 你的pushplus_token

[mail]
provider = smtp
smtp_server = smtp.qq.com
smtp_port = 465
user = 你的QQ号@qq.com
pass = 邮箱授权码
mail_to = me@example.com
```

跑起来长这样（**只会测 `TEST` 里写了的那几个渠道**，正式配置里的渠道不受影响）：

```text
=== GLaDOS 通知测试 2026-10-11 23:21:46 ===
已读取 TEST：全局配置 4 项，账号块 0 个
== 通知测试模式 ==
测试用的通知配置来自：TEST secret

推送渠道：PushPlus
邮件收件人：te***@example.com
开始发送测试通知…
  Resend 发信成功：...
  PushPlus 推送成功
✅ 测试通知全部发送成功
```

- 测完把 `TEST` 这个 secret **删掉**，签到就恢复正常。
- 想临时停用又不想删：把值改成 `0` / `false` / `no` / `off` 也行。
- `TEST` 里没有写出任何有效渠道时，脚本会提示一段可照抄的示例，并以退出码 1 结束。
- 本地也可以触发：

  ```bash
  # 用 TEST 的值测试（把上面那段配置写进环境变量）
  TEST='[telegram]
  bot_token = ...
  chat_id = ...' python GLaDOS_Checkin.py

  # 或者 --test：没设 TEST 时，直接测试当前配置里的渠道
  python GLaDOS_Checkin.py --test
  ```

---

## 7. Secret 速查表

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 添加。

| Secret 名 | 用途 | 必填 |
| --- | --- | --- |
| `GLADOS_CONFIG` | 一个 secret 装下所有配置（含以上各渠道的段） | 与账号二选一 |
| `GLADOS_COOKIES` / `GLADOS_COOKIE` | 账号 Cookie | 与 `GLADOS_CONFIG` 二选一 |
| `MAIL_PROVIDER` | `smtp` / `resend`，一般不用写（自动判断） | 否 |
| `SMTP_SERVER` / `SMTP_PORT` | SMTP 服务器与端口，默认 `smtp.qq.com` / `465` | 用 SMTP 时 |
| `MAIL_USER` / `MAIL_PASS` | 邮箱账号 / 授权码 | 用 SMTP 时 |
| `RESEND_API_KEY` | Resend API Key | 用 Resend 时 |
| `MAIL_FROM` | 发件人 | 否 |
| `MAIL_TO` | 收件人，多个逗号分隔 | 否 |
| `TG_BOT_TOKEN` / `TG_CHAT_ID` | Telegram 机器人 Token / 会话 ID | 用 Telegram 时 |
| `SCT_SENDKEY` | Server酱 SendKey | 用 Server酱时 |
| `PUSHPLUS_TOKEN` | PushPlus Token | 用 PushPlus 时 |
| `TEST` | 值就是一份通知配置；存在时用它里面写的渠道发测试通知，不执行签到 | 否 |

---

## 8. 常见问题

**Q：可以同时开多个渠道吗？**
可以，全都发。同一个账号的结果会分别推到每个已配置的渠道。

**Q：只想用微信，不配邮箱行不行？**
行。不配 `mail_to`（或 `MAIL_TO`）就不会发邮件，只走 Telegram / Server酱 / PushPlus。

**Q：设置了 `TEST` 却不签到，是坏事吗？**
不是，这正是它的用途。确认通知没问题后，删掉 `TEST` secret 就会恢复正常签到。

**Q：Telegram 报 `chat not found`？**
多半是没先给 bot 发过消息，或者 `chat_id` 填错了。先给 bot 发个 `hi`，再用 `getUpdates` 确认真实 `chat.id`。

**Q：Server酱 / PushPlus 提示 token 无效？**
检查是否完整复制（Server酱以 `SCT` 开头，PushPlus 是 32 位字符串），以及免费额度是否已用完。

**Q：邮件报 `535 认证失败`？**
`pass` 填的必须是邮箱**授权码**而不是登录密码，并确认该邮箱已开启 SMTP 服务、`user` 是完整邮箱地址。
