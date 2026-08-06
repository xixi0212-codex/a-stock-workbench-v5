# A股交易工作台

一个可本地运行、也可通过 Docker 或 Render 分享的 V5.0 规则化 A 股工作台。包含外围环境、A股大盘、日线计划、30分钟执行、退出管理和浏览器本地持仓评估。

## 本地运行

```powershell
python app.py
```

打开 `http://127.0.0.1:8765/`。

## Docker

```powershell
docker build -t a-stock-workbench .
docker run --rm -p 8765:8765 -e DASHBOARD_PASSWORD="设置你的密码" a-stock-workbench
```

## Render 部署

1. 把本目录提交到你自己的 GitHub 仓库。
2. 在 Render 中选择 **New > Blueprint**，连接该仓库。
3. 部署时为 `DASHBOARD_PASSWORD` 设置访问密码。
4. 部署完成后，把 Render 提供的 HTTPS 地址分享给需要访问的人。

浏览器登录框的用户名可任意填写，密码使用 `DASHBOARD_PASSWORD`。免费行情接口可能延迟或限流，盘中结果需要收盘后复核。

持仓数据只保存在访问者自己的浏览器 `localStorage` 中，不随服务器或云端代码共享。外围指数只调整新开仓权限，不直接产生买卖信号。

## 公开使用前须知

- GitHub 只保存代码；`render.yaml` 会在 Render 创建真正运行的后端服务。
- 行情来自东方财富、新浪等公开接口，不是交易所授权的低延迟行情，可能延迟、限流或临时中断。
- 免费 Render 实例可能休眠，首次访问会有冷启动等待，不适合自动交易或下单。
- 建议设置 `DASHBOARD_PASSWORD`。公开分享同一个密码时，所有访问者拥有相同权限。
- “近期候选”先按全市场成交额初筛，再对最多 24 只高流动性样本运行 V5 规则，不代表全市场逐股扫描。
