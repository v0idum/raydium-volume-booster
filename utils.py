import requests
from solders.keypair import Keypair
from solders.pubkey import Pubkey


def extract_pool_info(pool_list: list, pool_id: str) -> dict:
    for pool in pool_list:
        if pool['id'] == pool_id:
            return pool
    raise Exception(f'{pool_id} pool not found!')


def fetch_pool_keys(pool_id: str):
    pools = requests.get('https://api.raydium.io/v2/sdk/liquidity/mainnet.json').json()
    amm_info = extract_pool_info(pools['unOfficial'] + pools['official'], pool_id)
    print('Pool info', amm_info)
    return {
        'amm_id': Pubkey.from_string(pool_id),
        'authority': Pubkey.from_string(amm_info['authority']),
        'base_mint': Pubkey.from_string(amm_info['baseMint']),
        'base_decimals': amm_info['baseDecimals'],
        'quote_mint': Pubkey.from_string(amm_info['quoteMint']),
        'quote_decimals': amm_info['quoteDecimals'],
        'lp_mint': Pubkey.from_string(amm_info['lpMint']),
        'open_orders': Pubkey.from_string(amm_info['openOrders']),
        'target_orders': Pubkey.from_string(amm_info['targetOrders']),
        'base_vault': Pubkey.from_string(amm_info['baseVault']),
        'quote_vault': Pubkey.from_string(amm_info['quoteVault']),
        'market_id': Pubkey.from_string(amm_info['marketId']),
        'market_base_vault': Pubkey.from_string(amm_info['marketBaseVault']),
        'market_quote_vault': Pubkey.from_string(amm_info['marketQuoteVault']),
        'market_authority': Pubkey.from_string(amm_info['marketAuthority']),
        'bids': Pubkey.from_string(amm_info['marketBids']),
        'asks': Pubkey.from_string(amm_info['marketAsks']),
        'event_queue': Pubkey.from_string(amm_info['marketEventQueue'])
    }


def new_account():
    return Keypair().secret()
