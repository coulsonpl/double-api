import os
import json
import re
import time
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging
import threading
import chardet
import httpx
import random
import string

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取环境变量值，支持大小写不敏感，空值返回默认值。
def get_env_value(key, default=None):
    value = os.getenv(key) or os.getenv(key.lower()) or os.getenv(key.upper())
    return default if value in [None, ''] else value

# 从环境变量读取代理设置（支持大小写）
http_proxy = get_env_value('HTTP_PROXY')
https_proxy = get_env_value('HTTPS_PROXY')

# 初始化全局变量和锁
last_key_index = -1
index_lock = threading.Lock()

double_bots = {}
proxies = {
    "http://": http_proxy or https_proxy,
    "https://": http_proxy or https_proxy,
}

class DoubleBot:
    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.token_expiration_time = 0

    async def refresh_token(self):
        url = "https://api.double.bot/api/auth/refresh"
        headers = {
            "Authorization": f"Bearer {self.api_key}"
        }
        async with httpx.AsyncClient(proxies=proxies) as client:
            try:
                # print(headers)
                response = await client.post(url, headers=headers)
                response.raise_for_status()

                data = response.json()
                # print(data)
                self.access_token = data.get("access_token")
                self.token_expiration_time = time.time() + 10 

                if self.access_token:
                    logging.info("Access token refreshed successfully.")
                else:
                    logging.warning("Access token not found in response.")

            except httpx.HTTPError as e:
                logging.error(f"Error refreshing access token: {e}")

    async def get_access_token(self):
        # logging.info(f"get_access_token token_expiration_time: {self.token_expiration_time}")
        if time.time() >= self.token_expiration_time:
            await self.refresh_token()
        return self.access_token

    async def initialize(self):
        await self.refresh_token()

async def fetch(req):
    if req.method == "OPTIONS":
        return create_options_response()

    try:
        body = await req.json()
        headers = await prepare_headers(req)
        response = await post_request(body, headers, req)
        return response
    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return web.Response(text=str(e), status=500)

def create_options_response():
    return web.Response(body="", headers={
        'Access-Control-Allow-Origin': '*', 
        'Access-Control-Allow-Headers': '*'
    }, status=204)

def prepare_data(client_data):
    # logging.info(f"prepare data with body: {body}")
    server_data = {
        "api_key": "null",
        "messages": [],
        "chat_model": ""
    }

    for message in client_data['messages']:
        role = message['role']
        content = message['content']

        if role == 'system':
            role = "user"

        server_message = {
            "role": role,
            "message": content,
            "codeContexts": []
        }
        server_data['messages'].append(server_message)

    model_mapping = {
        "gpt-4-turbo": "GPT4 Turbo",
        "claude-3-opus-20240229": "Claude 3 (Opus)",
        "llama-3-70B": "Llama 3 70B",
        "gpt-4-turbo-2024-04-09": "GPT4 Turbo (2024-04-09)"
    }

    requested_model = client_data.get("model", "")
    server_data["chat_model"] = model_mapping.get(requested_model, "GPT4 Turbo")

    return server_data

async def prepare_headers(req):
    authorization = req.headers.get('authorization')
    if authorization and authorization.lower().startswith('bearer '):
        global last_key_index
        with index_lock:
            keys = authorization[7:].split(',')
            last_key_index = (last_key_index + 1) % len(keys) if keys else 0
            authorization = keys[last_key_index]
    else:
        url_params = req.url.query
        authorization = url_params.get('key', '')
    # 获取accesstoken
    double_bot = double_bots.get(authorization) 
    if double_bot is None:
        double_bot = DoubleBot(authorization)
        double_bots[authorization] = double_bot
    headers = {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN",
        "Authorization": f"Bearer {await double_bot.get_access_token()}",
        "Double-Version": "2024-03-04",
    }
    return headers

# 尝试解析多个JSON字符串拼在一起的字符串
def extract_and_concatenate_texts(json_string):
    return concatenated_text

async def post_request(data, headers, req):
    async with ClientSession(trust_env=True) as session:
        return await send_request(session, data, headers, req)

async def send_request(session, data, headers, req):
    body = prepare_data(data)
    async with session.post('https://api.double.bot/api/v1/chat', json=body, headers=headers, proxy=http_proxy or https_proxy) as resp:
        if resp.status != 200:
            response_text = await resp.text()
            logging.error(f"Error from API: Status: {resp.status}, Body: {response_text}")
            return resp
        return await handle_response(data, resp, req)

async def handle_response(data, resp, req):
    if not data.get("stream"):
        response_text = await resp.text()
        return create_response(data, response_text)
    else:
        return await stream_response(resp, data, req)

def get_random_string(length):
    letters = string.ascii_letters + string.digits
    result_str = ''.join(random.choice(letters) for _ in range(length))
    return result_str

def create_response(data, response_text):
    wrapped_chunk = {
        "id": f"chatcmpl-{get_random_string(29)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": data["model"],
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": response_text or ''
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        },
        "system_fingerprint": None
    }
    return web.Response(text=json.dumps(wrapped_chunk, ensure_ascii=False), content_type='application/json', headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*',
    })

async def stream_response(resp, data, req):
    created = int(time.time())
    chat_id = f"chatcmpl-{get_random_string(29)}"

    writer = web.StreamResponse()
    writer.headers['Access-Control-Allow-Origin'] = '*'
    writer.headers['Access-Control-Allow-Headers'] = '*'
    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
    await writer.prepare(req)

    async for chunk in resp.content.iter_any():
        # 字符串解码
        encodings =  ['utf-8', 'latin-1', 'gbk', 'ascii', 'utf-16', 'gb2312', 'gb18030', 'big5', 'euc-jp', 'euc-kr', 'shift_jis', 'iso-8859-1', 'iso-8859-15', 'windows-1252', 'koi8-r', 'mac_cyrillic', 'utf-32']
        for encoding in encodings:
            try:
                chunk_str = chunk.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if chunk_str == None:
            encoding = chardet.detect(chunk)
            chunk_str = chunk.decode(encoding['encoding'], errors='ignore') # 忽略无法解码的字符
        
        # 处理返回结果
        content_text = chunk_str

        # 流式回复
        if content_text is not None and content_text != "":
            wrapped_chunk = { 
                "id": chat_id, "object": "chat.completion.chunk", 
                "created": created, 
                "model": data["model"], 
                "choices": [
                    { "index": 0, "delta": { "role": "assistant", "content": content_text }, "finish_reason": None }
                ]
            }
            event_data = f"data: {json.dumps(wrapped_chunk, ensure_ascii=False)}\n\n"
            await writer.write(event_data.encode('utf-8'))

    # finish chunk
    finish_wrapped_chunk = { 
        "id": chat_id, "object": "chat.completion.chunk", 
        "created": created, 
        "model": data["model"], 
        "choices": [
            { "index": 0, "delta": {}, "finish_reason": 'stop' }
        ]
    }
    finish_event_data = f"data: {json.dumps(finish_wrapped_chunk, ensure_ascii=False)}\n\n"
    await writer.write(finish_event_data.encode('utf-8'))

    return writer

async def onRequest(request):
    return await fetch(request)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onRequest)

if __name__ == '__main__':
    port = int(get_env_value('SERVER_PORT', 3030))
    web.run_app(app, host='0.0.0.0', port=port)