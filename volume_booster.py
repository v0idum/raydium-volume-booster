import asyncio
import json
from typing import Any

from loguru import logger

from raydium_amm import Liquidity
from utils import fetch_pool_keys

config: Any

logger.add('bot.log', format="{time:YYYY-MM-DD HH:mm:ss},{level},{message}")

"""

{
  "ammPoolId": "7XawhbbxtsRcQA8KTkHT9f9nc6d69UwqCDh6U5EEbEmX",
  "symbol": "SOL/USDT",
  "walletSecretKeys": [],
  "pause": 15,
  "solanaEndpoint": "https://solana-api.projectserum.com"
}
"""


def load_conf():
    logger.info('Loading config...')
    global config
    with open('config.json') as f:
        config = json.load(f)


def save_conf(data):
    logger.info('Saving config...')
    with open('config.json', 'w') as f:
        json.dump(data, f)


async def boost(secret_key, pool_keys):
    print('Boost for ', secret_key)
    liq = Liquidity(config['solanaEndpoint'], pool_keys, secret_key, config['symbol'])
    await liq.init_accounts()
    logger.info(f'{liq.owner.pubkey()} wallet initialized')
    logger.info(f'Initial balances: {await liq.get_balance()}')
    tx_num = 0
    while True:
        balances = await liq.get_balance()
        if balances['quote'] > 0:
            await liq.buy(balances['quote'])
            balances = await liq.wait_for_updated_balance(balances)

            await asyncio.sleep(config['pause'])
            await liq.sell(balances['base'])
            tx_num += 2
        else:
            await liq.sell(balances['base'])
            balances = await liq.wait_for_updated_balance(balances)
            await asyncio.sleep(config['pause'])
            await liq.buy(balances['quote'])
            tx_num += 2
        await asyncio.sleep(config['pause'])


async def main():
    load_conf()
    pool_keys = fetch_pool_keys(config['ammPoolId'])
    tasks = []
    for key in config['walletSecretKeys']:
        tasks.append(asyncio.create_task(boost(key, pool_keys)))
    await asyncio.gather(*tasks)


if __name__ == '__main__':
    asyncio.run(main())
