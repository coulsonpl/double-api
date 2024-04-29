import os
import json
import random
import string
import time
import asyncio
import tiktoken
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse
import httpx
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 获取环境变量值，支持大小写不敏感，空值返回默认值。
def get_env_value(key, default=None):
    value = os.getenv(key) or os.getenv(key.lower()) or os.getenv(key.upper())
    return default if value in [None, ''] else value

# 从环境变量读取代理设置（支持大小写）
http_proxy = get_env_value('HTTP_PROXY')
https_proxy = get_env_value('HTTPS_PROXY')

app = FastAPI()

current_key_index = 0
double_bots = {}
proxies = {
    "http://": http_proxy or https_proxy,
    "https://": http_proxy or https_proxy,
}

def num_tokens_from_string(string: str, encoding_name: str) -> int:
    encoding = tiktoken.get_encoding(encoding_name)
    num_tokens = len(encoding.encode(string))
    return num_tokens

def count_token_messages(messages):
    count = 0
    for message in messages:
        content = message['content']
        count += content
    return count

# 创建 KeyManager 实例
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def verify_key(authorization: str = Header(...)):
    try:
        prefix, token = authorization.split()
        if prefix.lower() != "bearer" or token == "":
            raise HTTPException(status_code=400, detail="请填写正确的密钥")
        # 轮询到下一个API Key
        global current_key_index
        apikeys = token.split(',')
        current_key_index = (current_key_index + 1) % len(apikeys)
        current_key = apikeys[current_key_index]
        return current_key
    except ValueError:
        raise HTTPException(status_code=400, detail="请填写正确的密钥")

def transform_data(client_data):
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
                response = await client.post(url, headers=headers)
                response.raise_for_status()

                data = response.json()
                self.access_token = data.get("access_token")
                self.token_expiration_time = time.time() + 10 

                if self.access_token:
                    logging.info("Access token refreshed successfully.")
                else:
                    logging.warning("Access token not found in response.")

            except httpx.HTTPError as e:
                logging.error(f"Error refreshing access token: {e}")

    async def get_access_token(self):
        logging.info(f"get_access_token self.token_expiration_time: {self.token_expiration_time}")
        if time.time() >= self.token_expiration_time:
            await self.refresh_token()
        return self.access_token

    async def initialize(self):
        await self.refresh_token()

@app.post('/v1/chat/completions')
async def forward_request(request: Request, authorization: str = Depends(verify_key)):
    global target_url
    request_data = await request.json()
    messages = request_data.get('messages', [])
    total = ""
    for message in messages:
        total += message['content']
    prompt_tokens = num_tokens_from_string(total, "cl100k_base")
    target_url = "https://api.double.bot/api/v1/chat"

    # logging.info(f"authorization: {authorization}")
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
    print(target_url)
    server_data = transform_data(request_data)
    should_stream = request_data.get("stream", False)
    if should_stream:
        response_data = handle_streaming_and_sending(server_data, server_data["chat_model"], headers, target_url)
        return StreamingResponse(response_data, media_type="application/json")
    else:
        response_data = await generate_non_streaming_response(server_data, server_data["chat_model"], headers,
                                                              prompt_tokens)
        return response_data

def chunks(string, chunk_size):
    return [string[i:i + chunk_size] for i in range(0, len(string), chunk_size)]

def get_random_string(length):
    letters = string.ascii_letters + string.digits
    result_str = ''.join(random.choice(letters) for _ in range(length))
    return result_str

async def generate_response(request_data, models, headers, target_url):
    retry_count = 0
    max_retries = 3
    delay = 0.05
    i = 0
    same_id = f'chatcmpl-{get_random_string(29)}'
    while retry_count < max_retries and not i == 1:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=250), follow_redirects=True, proxies=proxies) as client:
                async with client.stream("POST", target_url, json=request_data, headers=headers) as res:
                    yield f"data: {json.dumps({'id': same_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': models, 'system_fingerprint': 'fp_a2448989', 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': 'null'}]})}\n\n".encode(
                        'utf-8')
                    if res.status_code == 200:
                        async for line in res.aiter_lines():
                            line = line + '\n'
                            if '```' in line:
                                line = '\n' + line
                            for char_in_line_chunked in list(chunks(line, 3)):
                                result = {
                                    "id": same_id,
                                    "object": "chat.completion.chunk",
                                    "created": int(time.time()),
                                    "model": models,
                                    "system_fingerprint": "fp_a24b4d720c",
                                    "choices": [
                                        {"index": 0, "delta": {"content": char_in_line_chunked},
                                         "finish_reason": "null"}
                                    ],
                                }
                                json_result = json.dumps(result, ensure_ascii=False)
                                yield f"data: {json_result}\n\n"
                                await asyncio.sleep(delay)
                        yield f"data: {json.dumps({'id': same_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': models, 'system_fingerprint': 'fp_a5689', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n".encode(
                            'utf-8')
                        yield "data: [DONE]\n".encode('utf-8')
                        i = 1
                    else:
                        print("重试一次")
                        logging.warning(f"Received non-200 status code {res.status_code}, retrying...")
                        retry_count += 1
        except (httpx.ReadTimeout, httpx.ConnectError) as e:
            error_message = f"Error during request: {e}"
            retry_count += 1

    return

async def generate_non_streaming_response(request_data, models, headers, prompt):
    n = request_data.get('n', 1)
    choices = []
    for i in range(n):
        retry_count = 0
        max_retries = 3
        data = ""
        global target_url, current_key
        while retry_count < max_retries:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(999, read=999), follow_redirects=True) as client:
                    res = await client.post(target_url, json=request_data, headers=headers)
                    if res.status_code == 200:
                        response_data = res.text
                        choice = {
                            "index": i,
                            "message": {
                                "role": "assistant",
                                "content": response_data
                            },
                            "finish_reason": "stop"
                        }
                        choices.append(choice)
                        break

                    else:
                        print("重试一次")
                        logging.warning(f"Received status code {res.status_code}: {res.text}, retrying...")
                        retry_count += 1
                    data = data + response_data
            except (httpx.ReadTimeout, httpx.ConnectError) as e:
                error_message = f"Error during request: {e}"
                retry_count += 1
        if retry_count == max_retries:
            return {"error": "Request failed after multiple retries"}
    prompt_tokens = prompt
    completion_tokens = sum(num_tokens_from_string(choice["message"]["content"], "cl100k_base") for choice in choices)
    total_tokens = prompt_tokens + completion_tokens

    response = {
        "id": "chatcmpl-92cWCMyLpYQqOiLf7tB8MAWopTLu1",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": models,
        "choices": choices,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        },
        "system_fingerprint": "fp_a24b4d720c"
    }

    return response

async def handle_streaming_and_sending(request_data, models, headers, target_url):
    data = ""
    async for item in generate_response(request_data, models, headers, target_url):
        try:
            data_json = json.loads(item[5:])
            if 'content' in data_json['choices'][0]['delta']:
                content = data_json['choices'][0]['delta']['content']
                data = data + content

        except json.JSONDecodeError:
            pass
        yield item  # 将每个项作为流的一部分

@app.on_event("startup")
async def startup_event():
    logging.info("app has been startup!")

if __name__ == "__main__":
    port = int(get_env_value('SERVER_PORT', 3030))
    uvicorn.run(app, host="0.0.0.0", port=port)
