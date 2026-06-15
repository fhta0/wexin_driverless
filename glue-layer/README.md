# 微信自动回复粘合层

连接 wx-cli (消息监控) 和 SightFlow (消息发送) 的 Python 粘合层。

## 依赖

- Python 3.8+
- wx-cli 已安装并初始化
- SightFlow 已启动并监听 127.0.0.1:12680

## 安装

```bash
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入你的 API Key 和微信 ID
```

## 运行

```bash
python main.py
```

## 流程

1. 每 5 秒调用 `wx new-messages --json --filter private,group`
2. 过滤自己发送的消息（防循环）
3. 翻译 wxid → 备注名/群名
4. 调用 AI 生成回复
5. 通过 SightFlow API 发送消息（串行化）