from flask import Flask, request, Response, jsonify, stream_with_context
import requests
import json
import sseclient
import urllib3
import uuid
import time
from typing import List, Dict, Any

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# 认证令牌
HIGHLIGHT_AUTH_TOKEN = "Bearer 此句替换为你的jwt"
HIGHLIGHT_API_URL = "https://chat-backend.highlightai.com/api/v1/chat"

# API 请求头
def get_highlight_headers():
    return {
        "accept": "*/*",
        "accept-encoding": "gzip, deflate, br, zstd",
        "accept-language": "zh-CN",
        "authorization": HIGHLIGHT_AUTH_TOKEN,
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Highlight/1.0.22 Chrome/126.0.6478.234 Electron/31.7.3 Safari/537.36",
        "sec-ch-ua": "\"Not/A)Brand\";v=\"8\", \"Chromium\";v=\"126\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
    }

@app.route('/v1/models', methods=['GET'])
def list_models():
    """返回可用模型列表"""
    return jsonify({
        "object": "list",
        "data": [
            {
                "id": "claude-3-7-sonnet",
                "object": "model",
                "created": 1717027200, 
                "owned_by": "anthropic"
            }
        ]
    })

@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """处理聊天完成请求"""
    data = request.json
    
    # 提取请求参数
    messages = data.get('messages', [])
    stream = data.get('stream', False)
    
    # 将 OpenAI 格式的消息转换为单个提示
    prompt = format_messages_to_prompt(messages)
    
    # 准备 Highlight 请求
    highlight_data = {
        "prompt": prompt,
        "attachedContext": [{"type": "aboutMe", "text": "[\"\\n\"]"}],
        "additionalTools": [],
        "backendPlugins": []
    }
    
    if stream:
        return stream_response(highlight_data)
    else:
        return non_stream_response(highlight_data)

def format_messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
    """将 OpenAI 格式的消息列表转换为单个提示字符串，保留角色前缀"""
    formatted_messages = []
    
    for message in messages:
        role = message.get('role', '')
        content = message.get('content', '')
        
        if role and content:
            # 保留角色前缀
            formatted_messages.append(f"{role}: {content}")
    
    # 将所有消息连接起来
    return "\n\n".join(formatted_messages)

def stream_response(highlight_data: Dict[str, Any]):
    """处理流式响应"""
    def generate():
        try:
            response = requests.post(
                HIGHLIGHT_API_URL,
                headers=get_highlight_headers(),
                json=highlight_data,
                stream=True,
                verify=False
            )
            
            if response.status_code != 200:
                yield f"data: {json.dumps({'error': {'message': f'Highlight API returned status code {response.status_code}', 'type': 'api_error'}})}\n\n"
                return
            
            client = sseclient.SSEClient(response)
            
            # 为这个响应创建一个唯一ID
            response_id = f"chatcmpl-{str(uuid.uuid4())}"
            created = int(time.time())
            
            # 发送初始 SSE 消息
            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created, 'model': 'claude-3-7-sonnet-20240620', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
            
            # 处理 SSE 事件
            for event in client.events():
                event_data = json.loads(event.data)
                
                if event_data.get("type") == "text":
                    content = event_data.get("content", "")
                    if content:
                        chunk = {
                            'id': response_id,
                            'object': 'chat.completion.chunk',
                            'created': created,
                            'model': 'claude-3-7-sonnet',
                            'choices': [
                                {
                                    'index': 0,
                                    'delta': {'content': content},
                                    'finish_reason': None
                                }
                            ]
                        }
                        yield f"data: {json.dumps(chunk)}\n\n"
            
            # 发送完成信息
            yield f"data: {json.dumps({'id': response_id, 'object': 'chat.completion.chunk', 'created': created, 'model': 'claude-3-7-sonnet-20240620', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            yield "data: [DONE]\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'server_error'}})}\n\n"
    
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

def non_stream_response(highlight_data: Dict[str, Any]):
    """处理非流式响应"""
    try:
        response = requests.post(
            HIGHLIGHT_API_URL,
            headers=get_highlight_headers(),
            json=highlight_data,
            stream=True,
            verify=False
        )
        
        if response.status_code != 200:
            return jsonify({
                'error': {
                    'message': f'Highlight API returned status code {response.status_code}',
                    'type': 'api_error'
                }
            }), response.status_code
        
        client = sseclient.SSEClient(response)
        
        # 收集完整响应
        full_response = ""
        for event in client.events():
            event_data = json.loads(event.data)
            if event_data.get("type") == "text":
                full_response += event_data.get("content", "")
        
        # 创建 OpenAI 格式的响应
        response_id = f"chatcmpl-{str(uuid.uuid4())}"
        created = int(time.time())
        
        return jsonify({
            'id': response_id,
            'object': 'chat.completion',
            'created': created,
            'model': 'claude-3-7-sonnet',
            'choices': [
                {
                    'index': 0,
                    'message': {
                        'role': 'assistant',
                        'content': full_response
                    },
                    'finish_reason': 'stop'
                }
            ],
            'usage': {
                'prompt_tokens': -1,  
                'completion_tokens': -1,  
                'total_tokens': -1  
            }
        })
        
    except Exception as e:
        return jsonify({
            'error': {
                'message': str(e),
                'type': 'server_error'
            }
        }), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)



