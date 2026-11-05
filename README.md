# sub-butler · 订阅账单自动管家

> 自动读取邮箱里的账单邮件，用大模型提取信息，同步到你的 Notion 订阅库，并在到期前提醒你。
>
> *Reads billing emails over IMAP, extracts subscription details with an LLM, keeps a Notion subscription database up to date, and reminds you before renewals and trial expirations.*

AI 工具、VPS、会员、手机号保号……订阅一多，手动记账很快就跟不上：新买的服务器忘了录，续费日过了没改，试用到期被扣款，API 额度用完才发现。

sub-butler 每天自动做这些事：

1. **扫描邮箱**：通过 IMAP 读取新邮件，先用关键词预筛，只把可能是账单的邮件交给大模型。
2. **AI 提取**：识别是新购买、续费、试用、取消、到期、扣款失败还是额度用完，并提取服务商、金额、币种、周期和下次续费日。
3. **同步 Notion**：自动匹配已有条目，续费就更新日期，新订阅就新建一行，只改需要改的字段。
4. **滚动续费日**：自动续费的订阅过了续费日，按周期往后推，库里永远是下一次的日期。
5. **到期提醒**：提前 N 天推送到 Telegram、Bark（iOS）、Server 酱（微信）或邮件。需要手动续费的条目过期了也会提醒。
6. **月报**：按分类汇总每月固定支出，多币种统一折算成人民币或其他货币。

```
📅 订阅提醒 2026-11-08
⚠️ 已过期 / 待确认（处理完记得在 Notion 里改日期）：
· 手机号保号 · 11-01（已过 7 天） · $1 · 需手动处理
⏰ 即将到期：
· Qoder Pro 试用 · 11-10（2 天后） · 试用结束 · 需手动处理
```

## 特点

- **用你现有的 Notion 库**：字段名、选项名都可以映射，不用迁移数据。默认配置适配中文字段（产品名称、服务商、续费/到期日……）。
- **任意邮箱**：用 IMAP，Gmail、Outlook、QQ 邮箱、163 都能用，不需要申请 Google Cloud 项目。
- **任意大模型**：兼容 OpenAI 接口，默认 DeepSeek，每天的费用通常不到 1 分钱。
- **自托管，数据不经过第三方**：跑在你自己的 VPS、NAS 或电脑上，只有预筛后的账单邮件正文会发给你选的大模型。
- **安全默认值**：Notion 只更新改动的字段；登录账号、支付方式、管理链接这些字段只读不写；`--dry-run` 可以先预览。遇到新的服务商或币种时，只在对应单选字段里追加选项，已有选项不删除、不改名。

## 快速开始

### 1. 准备三样东西

| 需要什么 | 去哪里拿 |
| --- | --- |
| **Notion 集成 Token** | 打开 [notion.so/profile/integrations](https://www.notion.so/profile/integrations) 新建一个内部集成，复制 Secret。然后在订阅数据库页面右上角 `···` → **Connections** 里添加这个集成。 |
| **邮箱 IMAP 密码** | Gmail：先开启两步验证，再到 [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) 生成「应用专用密码」。QQ / 163 邮箱：在设置里开启 IMAP 并生成授权码。 |
| **大模型 API Key** | 例如 [DeepSeek 开放平台](https://platform.deepseek.com/)，或任何 OpenAI 兼容服务。 |

还没有订阅库？在 Notion 里新建一个数据库，按下文「[Notion 字段](#notion-字段)」建好列即可。

### 2. 安装

```bash
git clone https://github.com/<你的用户名>/sub-butler.git
cd sub-butler
bash deploy/install.sh 09:00   # 创建虚拟环境、安装，并添加每天 9 点运行的 cron
```

也可以手动安装：`pip install .`，或者用 Docker（见下文）。

### 3. 配置

编辑 `config.yaml`（从 `config.example.yaml` 复制而来）和 `.env`：

```yaml
mail:
  username: you@gmail.com
  password: ${IMAP_PASSWORD}
  folder: "[Gmail]/All Mail"   # 中文界面的 Gmail 是 "[Gmail]/所有邮件"
notion:
  token: ${NOTION_TOKEN}
  database_id: 数据库链接里那串 32 位 ID
notify:
  telegram: {bot_token: ${TG_BOT_TOKEN}, chat_id: "你的 chat id"}
```

### 4. 检查并试运行

```bash
.venv/bin/sub-butler check                         # 检查 Notion、邮箱、大模型能否连上
.venv/bin/sub-butler sync --dry-run --since 2026-10-01   # 预览会改什么，不写入
.venv/bin/sub-butler sync --since 2026-10-01             # 确认没问题后正式同步
```

之后 cron 每天会自动执行 `sub-butler run`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `sub-butler check` | 检查配置和三方连接 |
| `sub-butler sync [--dry-run] [--since 日期]` | 扫描邮件并同步到 Notion，有变化时推送摘要 |
| `sub-butler remind [--days N] [--dry-run]` | 推送 N 天内到期和已过期的条目 |
| `sub-butler report [--send]` | 生成月报，加 `--send` 推送 |
| `sub-butler run` | 每日任务：同步 → 提醒 →（每月指定日）月报 |

## Notion 字段

默认字段名如下，可以在 `config.yaml` 的 `notion.fields` 里改成你自己的列名。数据库里没有的列会自动跳过。

| 逻辑字段 | 默认列名 | Notion 类型 | 说明 |
| --- | --- | --- | --- |
| name | 产品名称 | 标题 | |
| provider | 服务商 | 单选 | 新服务商会自动成为新选项 |
| category | 分类 | 单选 | 大模型会从已有选项里选，如 AI / VPS / 通讯 |
| kind | 类型 | 单选 | 如 订阅 / API 额度 / 免费权益 |
| plan | 套餐/等级 | 文本 | |
| amount | 费用 | 数字 | 实付金额 |
| currency | 币种 | 单选 | USD / CNY / HKD …… |
| cycle | 计费周期 | 单选 | 月付 / 每两月 / 季付 / 年付 / 按量 / 一次性 |
| due | 续费/到期日 | 日期 | |
| status | 状态 | 单选 | 使用中 / 试用中 / 已取消 / 已过期 |
| auto_renew | 自动续费 | 复选框 | |
| notes | 备注 | 文本 | 每次同步追加一行摘要 |
| account / payment_method / manage_url | 登录账号 / 支付方式 / 管理链接 | 邮箱 / 文本 / 链接 | 只读 |

英文库示例：

```yaml
notion:
  fields: {name: Name, provider: Provider, due: Renewal Date, status: Status, cycle: Billing Cycle}
  values:
    status: {active: Active, trial: Trial, cancelled: Cancelled, expired: Expired}
    cycle: {monthly: Monthly, yearly: Yearly, usage: Usage, once: One-time}
```

## Docker

```bash
cp config.example.yaml config.yaml && cp .env.example .env   # 编辑这两个文件
docker compose run --rm sub-butler check
docker compose run --rm sub-butler sync --dry-run
# 宿主机 crontab：
# 0 9 * * * cd /path/to/sub-butler && docker compose run --rm sub-butler run >> data/cron.log 2>&1
```

## 隐私

- 所有密钥只保存在你自己的机器上（`config.yaml` / `.env` 已被 `.gitignore` 忽略）。
- 邮件以只读方式读取（`BODY.PEEK`），不会被标记为已读。
- 只有通过关键词预筛的邮件才会发给大模型。正文里的链接会被替换成 `<url>`，并截断到 6000 字。可以用 `exclude_senders` 排除敏感发件人，比如银行和交易所。
- 提示词要求大模型的摘要里不包含卡号、密码、验证码、IP。

## 开发

```bash
pip install -e ".[dev]"
pytest
```

代码结构：

```
src/sub_butler/
  mail.py      IMAP 读取、关键词预筛、HTML 转文本
  extract.py   大模型提取（OpenAI 兼容接口）
  notion.py    Notion API（2025-09-03 data source 接口）与字段映射
  sync.py      匹配已有订阅、应用事件、续费日滚动
  remind.py    到期提醒、月报
  notify.py    Telegram / Bark / Server 酱 / 邮件
  cli.py       命令行
```

## Roadmap

- [ ] Gmail API 方式（无需应用专用密码）
- [ ] 解析 PDF 账单附件
- [ ] VPS 剩余价值计算（方便转让）
- [ ] 低余额预警：直接查询 API 余额接口
- [ ] 网页看板

欢迎提 Issue 和 PR。

## License

MIT
