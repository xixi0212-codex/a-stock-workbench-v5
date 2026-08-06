# 给 TraeWork 的部署说明

请将本项目部署为一个公网 Python Web 服务。

- Python 版本：3.12
- 安装命令：`pip install -r requirements.txt`
- 启动命令：`python app.py`
- 服务监听：`0.0.0.0`
- 端口：使用平台提供的 `PORT` 环境变量
- 健康检查：`/api/health`
- 建议设置环境变量：`DASHBOARD_PASSWORD=自定义访问密码`
- 可选限流：`DASHBOARD_RATE_LIMIT=40`

项目也包含 `Dockerfile` 和 `render.yaml`，平台支持 Docker 时可直接构建。部署后请返回公网 HTTPS 地址。行情来自公共免费接口，必须允许服务访问东方财富、新浪财经等外部数据源。

这是 V5.0 双视图版本，包含“行情研判”和“我的持仓”。持仓使用浏览器本地存储，部署平台不得把持仓写入公共数据库或日志。外围环境需要允许访问东方财富全球指数接口。
