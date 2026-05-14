# 腾讯云服务器抢购助手

基于 Python 的腾讯云轻量应用服务器抢购工具，支持 Web 页面扫码登录、任务查询、倒计时和自动抢购。

![Tencent Cloud](image.png)

## 功能特点

- **Web 页面操作**：通过浏览器创建抢购任务、查询任务状态。
- **页面内扫码登录**：后端使用 Playwright 打开腾讯云登录页，页面显示二维码截图。
- **多任务并发**：不同用户 id 对应独立任务、cookie 和结果。
- **自动计算 Token**：根据 cookie 中的 `skey` 自动计算 `x-csrf-token`。
- **固定场次抢购**：每天北京时间 `10:00` 和 `15:00` 自动选择下一场。
- **倒计时展示**：页面实时显示下一场抢购倒计时。
- **取消任务**：支持取消等待中的抢购任务。

## 环境要求

- Python 3.11 推荐
- Windows / macOS / Linux
- Playwright Chromium

Windows 部署请优先查看：[README-WINDOWS.md](README-WINDOWS.md)

## 安装依赖

建议使用 conda：

```bash
conda create -y -n tencentyun-snake-up python=3.11
conda activate tencentyun-snake-up
pip install -r requirements.txt
python -m playwright install chromium
```

## 启动 Web 服务

```bash
python -m uvicorn web_server:app --host 127.0.0.1 --port 8000
```

启动后访问：

```text
http://127.0.0.1:8000
```

如果需要局域网或内网穿透访问，可以改为：

```bash
python -m uvicorn web_server:app --host 0.0.0.0 --port 8000
```

## 页面使用流程

### 开始抢购

1. 输入唯一 id，推荐使用邮箱。
2. 点击“开始抢购”。
3. 后台打开 Chromium 浏览器。
4. 页面显示腾讯云登录二维码。
5. 手机扫码登录腾讯云。
6. 登录成功后页面显示任务状态和下一场倒计时。
7. 到每天北京时间 `10:00` 或 `15:00` 自动抢购。
8. 抢购完成后页面显示成功或失败结果。

### 查询任务

1. 输入已创建任务的 id。
2. 点击“查询”。
3. 页面显示任务状态、倒计时或抢购结果。

### 取消抢购

1. 查询或创建任务后，点击“取消抢购”。
2. 后台任务会进入 `canceled` 状态。

## 后端接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/tasks` | 创建抢购任务 |
| `GET` | `/api/tasks/{user_id}` | 查询任务状态 |
| `GET` | `/api/tasks/{user_id}/qr` | 获取二维码截图 |
| `GET` | `/api/tasks/{user_id}/events` | 订阅任务状态事件 |
| `POST` | `/api/tasks/{user_id}/cancel` | 取消抢购任务 |
| `POST` | `/api/admin/clear` | 清空本地所有任务数据 |

清空本地任务数据：

```bash
curl -X POST http://127.0.0.1:8000/api/admin/clear
```

注意：该接口不会暴露在前端页面，但会删除 `data/tasks/` 下的任务状态、二维码截图、cookie 和结果。

## 数据存储

任务数据保存在：

```text
data/tasks/
```

每个任务按用户 id hash 后独立保存：

```text
data/tasks/<user_id_hash>/
├── state.json
├── cookies.json
├── qr.png
└── result.json
```

`data/` 已加入 `.gitignore`，不要提交其中内容。

## 抢购策略

- 抢购场次：每天北京时间 `10:00` 和 `15:00`。
- 登录成功后自动选择下一场。
- 秒杀前 5 分钟重新读取 cookie。
- 根据 cookie 中的 `skey` 计算 `x-csrf-token`。
- 到点后并发抢购地域 `[1, 4, 8]`。

## 命令行备用方式

项目仍保留原始脚本：

```bash
python get_cookies.py
python snap_up_server.py
```

说明：

- `get_cookies.py` 会打开浏览器扫码登录，并保存 `cookies.json`。
- `snap_up_server.py` 会读取 `cookies.json`，等待下一场 `10:00` 或 `15:00` 抢购。
- `x-csrf-token` 会自动从 `skey` 计算，不需要手动填写。

## 常见问题

### 二维码截图慢或截偏

当前登录流程使用可见 Chromium：

```python
headless=False
```

如果浏览器被最小化、遮挡或系统限制后台渲染，截图可能变慢或异常。建议不要最小化后台 Chromium。

### 提示 id 已存在

同一个 id 只能创建一次任务。可以：

- 输入该 id 后点击“查询”。
- 调用 `/api/admin/clear` 清空本地任务数据后重新创建。

### 抢购失败

可能原因：

- cookie 已失效。
- 账号无购买权限。
- 商品或地域无库存。
- 腾讯云接口返回异常。

## 文件结构

```text
tencentyun-snake-up/
├── web_server.py          # Web 后端服务
├── web_static/            # Web 前端页面
├── get_cookies.py         # 命令行 Cookie 获取脚本
├── snap_up_server.py      # 命令行抢购脚本
├── requirements.txt       # Python 依赖
├── README-WINDOWS.md      # Windows 部署说明
├── design/                # 设计文档
├── image.png              # 演示图片
└── README.md              # 项目说明
```

## 免责声明

本项目仅供学习交流使用，请勿用于商业用途。使用本工具产生的任何后果由使用者自行承担。
