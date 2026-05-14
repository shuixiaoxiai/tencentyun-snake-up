# Windows 部署说明

本文档说明如何在 Windows 上部署并运行 Web 化抢购服务。

## 环境要求

- Windows 10 或 Windows 11
- Miniconda 或 Anaconda
- Git
- 稳定网络

建议使用 PowerShell 执行以下命令。

## 1. 克隆项目

```powershell
git clone https://github.com/shuixiaoxiai/tencentyun-snake-up.git
cd tencentyun-snake-up
```

## 2. 创建 conda 环境

```powershell
conda create -y -n tencentyun-snake-up python=3.11
conda activate tencentyun-snake-up
```

如果 `python=3.11` 解析失败，可以改用 conda-forge：

```powershell
conda create -y -n tencentyun-snake-up -c conda-forge python=3.11
conda activate tencentyun-snake-up
```

## 3. 安装依赖

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

如果下载 Chromium 较慢，可以多执行一次：

```powershell
python -m playwright install chromium
```

## 4. 启动 Web 服务

```powershell
python -m uvicorn web_server:app --host 127.0.0.1 --port 8000
```

启动成功后，在浏览器打开：

```text
http://127.0.0.1:8000
```

## 5. 页面使用流程

1. 输入唯一 id，推荐使用邮箱。
2. 点击“开始抢购”。
3. 后台会打开一个 Chromium 浏览器。
4. 页面中会显示腾讯云登录二维码。
5. 使用手机扫码登录腾讯云。
6. 登录成功后，页面隐藏二维码区域，显示任务状态和下一场倒计时。
7. 到每天北京时间 `10:00` 或 `15:00` 自动抢购。
8. 抢购完成后页面显示成功或失败结果。

如果已经创建过任务，可以输入同一个 id，点击“查询”查看状态。

## 6. 局域网访问

如果希望局域网其他设备访问，需要把启动命令改为：

```powershell
python -m uvicorn web_server:app --host 0.0.0.0 --port 8000
```

然后在 Windows 防火墙中允许 Python 或端口 `8000` 入站访问。

局域网访问地址示例：

```text
http://你的电脑局域网IP:8000
```

## 7. 内网穿透

项目本身只负责启动本地 Web 服务。

如果需要公网访问，可以使用内网穿透工具把本机 `8000` 端口暴露出去。

注意：

- 当前版本不做访问密码。
- 不要把服务地址公开给不可信的人。
- 页面不会展示 cookie 或 token，但服务端本地会保存登录 cookie。

## 8. 数据存储

任务数据会保存在：

```text
data/tasks/
```

其中包括：

- 任务状态
- 二维码截图
- 腾讯云 cookie
- 抢购结果

`data/` 已被 `.gitignore` 忽略，不应提交到 Git。

## 9. 清空本地任务数据

后端提供一个清空接口，不暴露在前端页面：

```powershell
curl -X POST http://127.0.0.1:8000/api/admin/clear
```

该接口会删除 `data/tasks/` 下的任务状态、二维码截图、cookie 和抢购结果。

## 10. 常见问题

### 二维码截图很慢

当前登录流程使用可见 Chromium 浏览器：

```python
headless=False
```

如果浏览器被最小化、遮挡或系统限制后台渲染，二维码截图可能变慢或异常。

建议：

- 不要最小化后台 Chromium。
- 等二维码出现后再扫码。
- 如果截图异常，刷新页面后重新查询任务。

### 页面显示旧二维码

新版本前端会在切换 id 时清空旧二维码。

如果浏览器缓存导致异常，可以按 `Ctrl + F5` 强制刷新页面。

### 端口被占用

如果 `8000` 端口被占用，可以换一个端口：

```powershell
python -m uvicorn web_server:app --host 127.0.0.1 --port 8001
```

然后访问：

```text
http://127.0.0.1:8001
```

### Windows 防火墙拦截

如果局域网或内网穿透访问失败，检查：

- Windows 防火墙是否允许 Python 入站。
- 内网穿透工具是否指向正确端口。
- 本机服务是否使用 `--host 0.0.0.0` 启动。

## 11. 停止服务

在运行服务的 PowerShell 窗口按：

```text
Ctrl + C
```

即可停止服务。
