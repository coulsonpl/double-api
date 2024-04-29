# double-api

### 运行容器
运行容器，可以通过环境变量传递配置信息，例如代理URL和监听的端口。以下是一个示例：

```
version: '3'
services:
  double-api:
    container_name: double-api
    image: coulsontl/double-api
    network_mode: bridge
    restart: always
    ports:
      - '3030:3030'
    environment:
      - TZ=Asia/Shanghai
      - HTTP_PROXY=http://user:password@yourproxy:port
      - SERVER_PORT=3030
```

### 环境变量说明
* HTTP_PROXY: 指定所有请求通过的代理服务器的URL
* SERVER_PORT: 代表监听的端口，默认3030

### 支持传多个KEY进行轮询
```
curl --location 'http://127.0.0.1:3030/v1/chat/completions' \
--header 'Content-Type: application/json' \
--header 'Authorization: Bearer key1,key2,key3' \
--data '{
     "model": "gpt-4-turbo",
     "stream": false,
     "messages": [{"role": "user", "content": "Say this is a test!"}]
   }'
```
