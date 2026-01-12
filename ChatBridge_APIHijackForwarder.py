"""
sequenceDiagram
    participant User as external application
    participant UserAPI as user interface
    participant WS as WebSocket
    participant ST as SillyTavern
    participant STAPI as ST interface
    participant LLMAPI as LLM interface
    participant LLM as external LLM

    USER, ST, and LLM are all external logic. This script should not contain specific implementations.
    
    User->>UserAPI: 1. Call API (OpenAI format)
    UserAPI->>WS: 2. Forward the request to WebSocket
    WS->>ST: 3. Notify ST to process the request
    ST->>STAPI: 4. Call the ST interface after processing
    STAPI->>LLMAPI: 5. Forward to LLM interface
    LLMAPI->>LLM: 6. Call external LLM
    LLM->>LLMAPI: 7. Return response
    LLMAPI-┬->> STAPI: 8a. Forward response
           └- UserAPI: 8b. Forward responses simultaneously
    STAPI->> ST: 9a. Return to ST
    UserAPI->>User: 9b. Return to user
"""

# "REMOVED",
#   "base_url": "https://api.aiuvdt.top",
#   "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",

import json
import asyncio
import websockets
import aiohttp
import logging
import os
from collections import deque
import uuid
from aiohttp import web
from typing import List, Dict, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class APIKeyRotator:
    def __init__(self, api_keys: List[str]):
        self.api_keys = deque(api_keys)
    
    def get_next_key(self) -> str:
        current_key = self.api_keys[0]
        self.api_keys.rotate(-1)
        return current_key

class ChatBridgeForwarder:
    def __init__(self, settings_path: str):
        with open(settings_path, 'r') as f:
            self.settings = json.load(f)
        
        self.ws_clients = set()
        self.key_rotator = APIKeyRotator(self.settings['llm_api']['api_keys'])
        self.response_futures = {}
        
    async def start(self):
        # Start websocket server
        ws_server = websockets.serve(
            self.handle_websocket,
            self.settings['websocket']['host'],
            self.settings['websocket']['port']
        )

        # Create ST API server
        st_app = web.Application()
        # Modify routing processing
        st_app.router.add_get('/models', self.handle_models)
        st_app.router.add_get('/v1/models', self.handle_models)
        st_app.router.add_post('/chat/completions', self.handle_chat_completions)
        st_app.router.add_post('/v1/chat/completions', self.handle_chat_completions)
            
        # Initialize ST API server
        st_runner = web.AppRunner(st_app)
        await st_runner.setup()
        st_site = web.TCPSite(
            st_runner,
            self.settings['st_api']['host'],
            self.settings['st_api']['port']
        )

        # Create user api server
        user_app = web.Application()
        user_app.router.add_post('/v1/chat/completions', self.handle_user_api)
        user_runner = web.AppRunner(user_app)
        await user_runner.setup()
        user_site = web.TCPSite(
            user_runner,
            self.settings['user_api']['host'],
            self.settings['user_api']['port']
        )

        # Start all servers
        await asyncio.gather(
            ws_server,
            st_site.start(),
            user_site.start()
        )
        
        logger.info(f"The Websocket Server runs on ws://{self.settings['websocket']['host']}:{self.settings['websocket']['port']}")
        logger.info(f"The ST API Serber rubs on http://{self.settings['st_api']['host']}:{self.settings['st_api']['port']}")
        logger.info(f"The user api server runs on http://{self.settings['user_api']['host']}:{self.settings['user_api']['port']}")

    async def handle_websocket(self, websocket):
        self.ws_clients.add(websocket)
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    logger.info(f"Receive websocket message: {data}")
                    
                    # Handle ST's response
                    if data.get('type') == 'st_response':
                        request_id = data.get('id')
                        if request_id in self.response_futures:
                            future = self.response_futures[request_id]
                            if not future.done():
                                future.set_result(data.get('content'))
                                
                except json.JSONDecodeError:
                    logger.error("Invalid websocket message format")
        finally:
            self.ws_clients.remove(websocket)

    async def handle_user_api(self, request: web.Request) -> web.Response:
        """Handle api requests from users"""
        if request.headers.get('Authorization') != f"Bearer {self.settings['user_api']['api_key']}":
            return web.Response(status=401)

        try:
            request_data = await request.json()
            request_id = str(uuid.uuid4())
            is_stream = request_data.get('stream', False)
            logger.info(f"user api request ID={request_id}, stream={is_stream}")

            if is_stream:
                # Create streaming responses
                stream_response = web.StreamResponse(
                    status=200,
                    headers={
                        'Content-Type': 'text/event-stream',
                        'Cache-Control': 'no-cache',
                        'Connection': 'keep-alive'
                    }
                )
                await stream_response.prepare(request)
                
                # Create event queue
                queue = asyncio.Queue() 
                self.response_futures[request_id] = queue

                try:
                    # Send websocket message
                    ws_message = {
                        'type': 'user_request',
                        'id': request_id,
                        'content': request_data
                    }
                    
                    if not self.ws_clients:
                        return web.Response(status=503, text="No WebSocket clients connected")
                    
                    for ws in self.ws_clients:
                        try:
                            await ws.send(json.dumps(ws_message))
                            logger.info(f"Request sent to websocket: ID={request_id}")
                            break
                        except Exception as e:
                            logger.error(f"Failed to send websocket message: {e}")
                            continue

                    # Wait for and forward response chunk
                    received_chunks = []
                    while True:
                        try:
                            chunk = await asyncio.wait_for(queue.get(), timeout=60.0)
                            
                            # Only process non-empty valid data
                            if chunk and isinstance(chunk, str):
                                chunk = chunk.strip()
                                if not chunk:
                                    continue
                                    
                                if chunk == '[DONE]':
                                    await stream_response.write(b'data: [DONE]\n\n')
                                    logger.info(f"Send streaming response end tag: ID={request_id}")
                                    break
                                    
                                # Make sure the response is well formatted
                                if not chunk.startswith('data: '):
                                    chunk = f'data: {chunk}'
                                if not chunk.endswith('\n\n'):
                                    chunk = f'{chunk}\n\n'
                                    
                                logger.debug(f"Send response block: {chunk.strip()}")
                                await stream_response.write(chunk.encode())
                                
                        except asyncio.TimeoutError:
                            logger.warning(f"Timeout waiting for response block: ID={request_id}")
                            await stream_response.write(b'data: [DONE]\n\n')
                            break
                            
                    return stream_response
                    
                finally:
                    # clear queue
                    self.response_futures.pop(request_id, None)
            else:
                # Handle non-streaming requests
                future = asyncio.Future()
                self.response_futures[request_id] = future
                
                # Send websocket message
                ws_message = {
                    'type': 'user_request',
                    'id': request_id,
                    'content': request_data
                }
                
                if not self.ws_clients:
                    return web.Response(status=503, text="No WebSocket clients connected")
                    
                for ws in self.ws_clients:
                    try:
                        await ws.send(json.dumps(ws_message))
                        logger.info(f"Request sent to websocket: ID={request_id}")
                        break
                    except Exception as e:
                        logger.error(f"Failed to send websocket message: {e}")
                        continue
                
                try:
                    # Waiting for response
                    response = await asyncio.wait_for(future, timeout=60.0)
                    return web.json_response(response)
                finally:
                    self.response_futures.pop(request_id, None)

        except Exception as e:
            logger.error(f"Failed to handle user api request: {str(e)}", exc_info=True)
            return web.Response(status=500, text=f"Internal Server Error: {str(e)}")
      
    async def handle_models(self, request: web.Request) -> web.Response:
        """Handle model list requests"""
        logger.info(f"Received models request: {request.path}")
        api_key = self.key_rotator.get_next_key()
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                #remove v1/ of llm_api
                target_url = f"{self.settings['llm_api']['base_url']}/models"
                logger.info(f"Forward the request to: {target_url}")
                async with session.get(target_url, headers=headers) as response:
                    response_data = await response.json()
                    logger.info(f"model list response: {response_data}")
                    return web.json_response(response_data)
        except Exception as e:
            logger.error(f"Failed to get model list: {str(e)}")
            return web.Response(status=500, text=str(e))

    async def handle_chat_completions(self, request: web.Request) -> web.Response:
        try:
            request_data = await request.json()
            is_stream = request_data.get('stream', False)
            logger.info(f"Receive chat completion request: PATH={request.path}, STREAM={is_stream}")

            # Find active user requests
            active_user_futures = {
                rid: future for rid, future in self.response_futures.items()
                if not getattr(future, 'done', lambda: True)()
            }
            
            if active_user_futures:
                logger.info(f"active user requests {len(active_user_futures)} active user requests")
            else:
                logger.warning("No active user request found")

            api_key = self.key_rotator.get_next_key()
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            target_url = f"{self.settings['llm_api']['base_url']}/chat/completions"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(target_url, json=request_data, headers=headers) as llm_response:
                    if llm_response.headers.get('content-type') == 'text/event-stream':
                        logger.info("Handling streaming responses")
                        st_response = web.StreamResponse(
                            status=llm_response.status,
                            headers={'Content-Type': 'text/event-stream'}
                        )
                        await st_response.prepare(request)

                        active_user_queues = {
                            rid: queue for rid, queue in self.response_futures.items() 
                            if isinstance(queue, asyncio.Queue)
                        }

                        async for chunk in llm_response.content:
                            if chunk:
                                chunk_str = chunk.decode()
                                logger.debug(f"data block received: {chunk_str[:100]}...")
                                
                                # send to ST
                                await st_response.write(chunk)
                                
                                # forward to user queue
                                if active_user_queues:
                                    for queue_id, queue in active_user_queues.items():
                                        try:
                                            await queue.put(chunk_str)
                                            logger.debug(f"Forward the data block to the user queue {queue_id}")
                                        except Exception as e:
                                            logger.error(f"Forwarding to user queue failed {queue_id}: {e}")

                        # Send end tag
                        if active_user_queues:
                            for queue_id, queue in active_user_queues.items():
                                try:
                                    await queue.put('[DONE]')
                                    logger.info(f"Send end tag to user queue {queue_id}")
                                except Exception as e:
                                    logger.error(f"Failed to send end tag {queue_id}: {e}")

                        return st_response

                    else:
                        logger.info("Handling non-streaming responses")
                        response_data = await llm_response.json()
                        logger.info(f"LLM response received: {str(response_data)[:200]}...")

                        # Forward to all pending user requests
                        futures_updated = False
                        for request_id, future in list(active_user_futures.items()):
                            try:
                                if isinstance(future, asyncio.Future) and not future.done():
                                    future.set_result(response_data)
                                    logger.info(f"Successfully set user request result: ID={request_id}")
                                    futures_updated = True
                            except Exception as e:
                                logger.error(f"Setting user request result failed {request_id}: {e}")

                        if not futures_updated:
                            logger.warning("No user-requested results were successfully updated.")

                        return web.json_response(response_data, status=llm_response.status)

        except Exception as e:
            error_msg = f"Failed to process chat completion request: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return web.Response(status=500, text=error_msg)
    
async def main():
    settings_path = os.path.join(os.path.dirname(__file__), 'settings.json')
    forwarder = ChatBridgeForwarder(settings_path)
    await forwarder.start()
    try:
        await asyncio.Future()  # Keep the server running
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    asyncio.run(main())
