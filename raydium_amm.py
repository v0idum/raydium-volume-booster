import asyncio
import re
import traceback
from ast import literal_eval

from loguru import logger
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Commitment
from solana.rpc.types import TokenAccountOpts
from solana.transaction import Transaction
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import transfer, TransferParams
from spl.token.constants import WRAPPED_SOL_MINT
from spl.token.instructions import create_associated_token_account, close_account, CloseAccountParams

from utils import new_account

SERUM_VERSION = 3
AMM_PROGRAM_VERSION = 4

AMM_PROGRAM_ID = Pubkey.from_string('675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8')
TOKEN_PROGRAM_ID = Pubkey.from_string('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
# SERUM_PROGRAM_ID = PublicKey('9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin')
SERUM_PROGRAM_ID = Pubkey.from_string('srmqPvymJeFKQ4zGQed1GFppgkRHL9kaELCbyksJtPX')

LIQUIDITY_FEES_NUMERATOR = 25
LIQUIDITY_FEES_DENOMINATOR = 10000


def compute_sell_price(pool_info):
    reserve_in = pool_info['pool_coin_amount']
    reserve_out = pool_info['pool_pc_amount']

    amount_in = 1 * 10 ** pool_info['coin_decimals']
    fee = amount_in * LIQUIDITY_FEES_NUMERATOR / LIQUIDITY_FEES_DENOMINATOR
    amount_in_with_fee = amount_in - fee
    denominator = reserve_in + amount_in_with_fee
    amount_out = reserve_out * amount_in_with_fee / denominator
    return amount_out / 10 ** pool_info['pc_decimals']


def compute_buy_price(pool_info):
    try:
        reserve_in = pool_info['pool_pc_amount']
        reserve_out = pool_info['pool_coin_amount']

        amount_in = 100 * 10 ** pool_info['pc_decimals']

        fee = amount_in * LIQUIDITY_FEES_NUMERATOR / LIQUIDITY_FEES_DENOMINATOR
        amount_in_with_fee = amount_in - fee
        denominator = reserve_in + amount_in_with_fee
        amount_out = reserve_out * amount_in_with_fee / denominator
        return 100 / (amount_out / 10 ** pool_info['coin_decimals'])
    except ZeroDivisionError:
        print('Amount out', amount_out, 'coin decimals', pool_info['coin_decimals'], 'reserve_in', reserve_in,
              'reserve_out', reserve_out)
        traceback.print_exc()


class Liquidity:

    def __init__(self, rpc_endpoint: str, pool_keys, secret_key: str, symbol: str):
        self.endpoint = rpc_endpoint
        self.conn = AsyncClient(self.endpoint, commitment=Commitment("confirmed"))
        self.pool_keys = pool_keys
        self.owner = Keypair.from_base58_string(secret_key)
        self.base_token_account = None
        self.quote_token_account = None
        self.base_symbol, self.quote_symbol = symbol.split('/')

    def open(self):
        self.conn = AsyncClient(self.endpoint, commitment=Commitment("confirmed"))

    async def close(self):
        await self.conn.close()

    async def change_wallet(self):
        while True:
            try:
                new_private_key = new_account()
                logger.info(f'New wallet secret key: {new_private_key}')
                new_owner = Keypair.from_base58_string(new_private_key)
                balance = (await self.conn.get_balance(self.owner.pubkey())).value
                logger.info(f'SOL Balance: {balance}')

                dummy_transaction = Transaction()
                dummy_transaction.add(transfer(TransferParams(
                    from_pubkey=self.owner.pubkey(),
                    to_pubkey=new_owner.pubkey(),
                    lamports=1137553539  # Dummy amount, will not be sent
                )))

                # Estimate the fee
                fee_estimate = (await self.conn.get_fee_for_message(dummy_transaction.compile_message())).value

                if fee_estimate is None:
                    print("Unable to estimate transaction fee. Using default fee calculation.")
                    fee_estimate = (await self.conn.get_minimum_balance_for_rent_exemption(1)).value

                print(f"Estimated transaction fee: {fee_estimate} lamports")

                # Calculate the amount to transfer after deducting the fee
                amount_to_transfer = balance - fee_estimate
                if amount_to_transfer <= 0:
                    print("Insufficient balance in the source account to cover the transaction fee.")
                    return

                transaction = Transaction()
                transaction.add(transfer(TransferParams(
                    from_pubkey=self.owner.pubkey(),
                    to_pubkey=new_owner.pubkey(),
                    lamports=amount_to_transfer
                )))
                res = await self.conn.send_transaction(transaction, self.owner)
                logger.info(f'Wallet Change tx hash: {res.value}')
                self.owner = new_owner
                balance = (await self.conn.get_balance(self.owner.pubkey())).value
                while balance == 0:
                    await asyncio.sleep(1)
                    balance = (await self.conn.get_balance(self.owner.pubkey())).value
                await self.init_accounts()
                break
            except Exception as e:
                logger.error(f'Error in change_wallet(): {e}')
                traceback.print_exc()

    async def init_accounts(self):
        self.base_token_account = await self.get_token_account(self.pool_keys['base_mint'])
        self.quote_token_account = await self.get_token_account(self.pool_keys['quote_mint'])

    async def init_account(self, mint: Pubkey):
        while True:
            try:
                if mint == WRAPPED_SOL_MINT:
                    print('One of the tokens is SOL, WRAPPING!')
                    await self.wrap_sol()
                    break
                inst = create_associated_token_account(self.owner.pubkey(), self.owner.pubkey(), mint)
                tx = Transaction().add(inst)
                signers = [self.owner]
                res = await self.conn.send_transaction(tx, *signers)
                await self.conn.confirm_transaction(res.value)
                break
            except Exception:
                await asyncio.sleep(5)

    async def get_token_account(self, mint: Pubkey):
        while True:
            print('Get token account', mint)
            try:
                account_data = await self.conn.get_token_accounts_by_owner(self.owner.pubkey(),
                                                                           TokenAccountOpts(mint))
                print('account_data', account_data)
                if not account_data.value:
                    print('No token account, creating...')
                    await self.init_account(mint)
                    account_data = await self.conn.get_token_accounts_by_owner(self.owner.pubkey(),
                                                                               TokenAccountOpts(mint))
                    if mint == WRAPPED_SOL_MINT:
                        while not account_data.value:
                            print('Still no token account, wrapping...')
                            account_data = await self.conn.get_token_accounts_by_owner(self.owner.pubkey(),
                                                                                       TokenAccountOpts(mint))
                return account_data.value[-1].pubkey
            except Exception as e:
                logger.exception(f'Exc in get_token_account() {e}')
                await asyncio.sleep(1.5)

    async def unwrap_sol(self):
        wsol_account_pubkey = await self.get_token_account(WRAPPED_SOL_MINT)
        logger.info(f'Unwrapping {wsol_account_pubkey}')
        # Create the close account instruction
        close_account_ix = close_account(CloseAccountParams(
            account=wsol_account_pubkey,
            dest=self.owner.pubkey(),
            owner=self.owner.pubkey(),
            program_id=TOKEN_PROGRAM_ID
        ))

        # Combine instructions into one transaction
        transaction = Transaction()
        transaction.add(close_account_ix)

        # Send the transaction
        response = await self.conn.send_transaction(transaction, self.owner)
        logger.success(f"Transaction signature: {response.value}")

        balance_before = (await self.conn.get_balance(self.owner.pubkey())).value

        balance = (await self.conn.get_balance(self.owner.pubkey())).value
        while balance == balance_before:
            await asyncio.sleep(1)
            balance = (await self.conn.get_balance(self.owner.pubkey())).value

    async def buy(self, amount):
        try:
            swap_tx = Transaction()
            signers = [self.owner]
            token_account_in = self.quote_token_account
            token_account_out = self.base_token_account
            amount_in = amount
            logger.info(f'[{self.owner.pubkey()}] Buying {self.base_symbol} with amount in: {amount}')
            swap_tx.add(
                self.make_swap_instruction(amount_in, token_account_in, token_account_out, self.pool_keys))
            res = await self.conn.send_transaction(swap_tx, *signers)
            logger.success(f'[{self.owner.pubkey()}] Bought, tx: {res.value}')
        except Exception as e:
            logger.exception(f'[{self.owner.pubkey()}] Exc in buy({amount}): {e}')

    async def sell(self, amount):
        try:
            swap_tx = Transaction()
            signers = [self.owner]
            token_account_in = self.base_token_account
            token_account_out = self.quote_token_account
            amount_in = amount
            logger.info(f'[{self.owner.pubkey()}] Selling {amount} {self.base_symbol}')
            swap_tx.add(
                self.make_swap_instruction(amount_in, token_account_in, token_account_out, self.pool_keys))
            res = await self.conn.send_transaction(swap_tx, *signers)
            logger.success(f'[{self.owner.pubkey()}] Sold, tx: {res.value}')
        except Exception as e:
            logger.exception(f'[{self.owner.pubkey()}] Exc in sell({amount}): {e}')

    async def simulate_get_market_info(self):
        recent_block_hash = (await self.conn.get_latest_blockhash()).value
        tx = Transaction(recent_blockhash=recent_block_hash, fee_payer=self.owner.pubkey())
        tx.add(self.make_simulate_pool_info_instruction(self.pool_keys))
        signers = [self.owner]
        tx.sign(*signers)
        # res = (await self.conn.simulate_transaction(tx))
        # print(res)
        res = (await self.conn.simulate_transaction(tx))['result']['value']['logs'][4]
        pool_info = literal_eval(re.search('({.+})', res).group(0))
        print(pool_info)
        return pool_info

    async def get_prices(self):
        pool_info = await self.simulate_get_market_info()
        # return round(compute_buy_price(pool_info), 4), round(compute_sell_price(pool_info), 4)
        return compute_buy_price(pool_info), compute_sell_price(pool_info)

    async def get_balance(self):
        try:
            bal = await self.conn.get_token_account_balance(self.base_token_account)
            base_token_balance = int(bal.value.amount)
            base_ui = bal.value.ui_amount
        except Exception:
            base_token_balance = 0
            base_ui = 0
        try:
            bal = await self.conn.get_token_account_balance(self.quote_token_account)
            quote_token_balance = int(bal.value.amount)
            quote_ui = bal.value.ui_amount
        except Exception:
            quote_token_balance = 0
            quote_ui = 0
        return {"base_ui": base_ui, 'quote_ui': quote_ui, 'base': base_token_balance, 'quote': quote_token_balance}

    async def wait_for_updated_balance(self, balance_before: dict):
        balance_after = await self.get_balance()
        while balance_after == balance_before or (balance_after['base'] == 0 and balance_after['quote'] == 0):
            await asyncio.sleep(1)
            balance_after = await self.get_balance()
        logger.info(f'[{self.owner.pubkey()}] Balances changed: {balance_before} -> {balance_after}')
        return balance_after
