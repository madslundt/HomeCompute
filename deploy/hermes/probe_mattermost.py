"""Read-only REST and WebSocket authentication proof; emits no credentials."""
import asyncio
import os
import aiohttp

URL = 'https://home-core.tail479ad.ts.net:9443'


async def main():
    token = os.environ['MATTERMOST_TOKEN']
    assert token.startswith('openshell:resolve:env:')
    async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(URL + '/api/v4/users/me', headers={'Authorization': 'Bearer ' + token}) as response:
            response.raise_for_status()
            identity = await response.json()
            assert identity['username'] == 'hermes-agent' and identity['is_bot']
        async with session.ws_connect(URL.replace('https:', 'wss:') + '/api/v4/websocket',
                                      proxy=os.environ['HTTPS_PROXY']) as websocket:
            await websocket.send_json({'seq': 1, 'action': 'authentication_challenge', 'data': {'token': token}})
            for _ in range(5):
                message = await websocket.receive_json(timeout=10)
                if message.get('seq_reply') == 1:
                    assert message.get('status') == 'OK'
                    print('{"rest_authenticated":true,"websocket_authenticated":true,"bot":"hermes-agent"}')
                    return
            raise RuntimeError('Mattermost authentication acknowledgement was not observed')


asyncio.run(main())
