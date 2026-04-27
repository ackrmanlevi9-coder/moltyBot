"""
ERC-8004 Identity Registry on-chain calls.
register() from Owner EOA -> returns tokenId -> POST /api/identity.
Uses PoA-enabled Web3 provider.

Docs say ERC-8004 gas is delegated/relayed. A raw RPC transaction is not
delegated, so this module first reuses any existing NFT and only then attempts
direct minting as a best-effort fallback.
"""
from web3 import Web3
from eth_account import Account
from bot.config import IDENTITY_REGISTRY, CROSS_CHAIN_ID
from bot.web3.contracts import IDENTITY_ABI
from bot.web3.provider import get_w3
from bot.utils.logger import get_logger

log = get_logger(__name__)

ZERO_ADDRESS_TOPIC = "0x" + "0" * 64
TRANSFER_TOPIC = Web3.keccak(text="Transfer(address,address,uint256)").hex()


def _address_topic(address: str) -> str:
    return "0x" + Web3.to_checksum_address(address)[2:].lower().rjust(64, "0")


def find_owned_identity_token(owner_address: str) -> int | None:
    """
    Find an already-minted ERC-8004 NFT owned by owner_address.
    This handles the case where minting succeeded elsewhere, but /api/identity was not saved.
    """
    try:
        w3 = get_w3()
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(IDENTITY_REGISTRY),
            abi=IDENTITY_ABI,
        )
        owner = Web3.to_checksum_address(owner_address)
        logs = w3.eth.get_logs({
            "fromBlock": 0,
            "toBlock": "latest",
            "address": Web3.to_checksum_address(IDENTITY_REGISTRY),
            "topics": [TRANSFER_TOPIC, ZERO_ADDRESS_TOPIC, _address_topic(owner)],
        })

        for event_log in reversed(logs):
            topics = event_log.get("topics", [])
            if len(topics) < 4:
                continue
            token_id = int(topics[3].hex(), 16)
            current_owner = registry.functions.ownerOf(token_id).call()
            if current_owner.lower() == owner.lower():
                log.info("Found existing ERC-8004 identity NFT: tokenId=%d", token_id)
                return token_id

    except Exception as e:
        log.warning("Could not scan existing ERC-8004 identity NFTs: %s", e)

    return None


async def register_identity_onchain(owner_private_key: str) -> int | None:
    """
    Ensure the Owner EOA has an ERC-8004 identity NFT.
    Returns tokenId (= agentId) or None if it cannot be resolved/minted.
    """
    acct = Account.from_key(owner_private_key)

    existing_token_id = find_owned_identity_token(acct.address)
    if existing_token_id is not None:
        return existing_token_id

    try:
        w3 = get_w3()
        registry = w3.eth.contract(
            address=Web3.to_checksum_address(IDENTITY_REGISTRY),
            abi=IDENTITY_ABI,
        )

        tx = registry.functions.register().build_transaction({
            "from": acct.address,
            "nonce": w3.eth.get_transaction_count(acct.address),
            "gas": 200000,
            "chainId": CROSS_CHAIN_ID,
        })

        signed = w3.eth.account.sign_transaction(tx, owner_private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt.status != 1:
            log.error("ERC-8004 register() TX failed: %s", tx_hash.hex())
            return None

        for event_log in receipt.logs:
            if len(event_log.topics) >= 4:
                token_id = int(event_log.topics[3].hex(), 16)
                log.info("ERC-8004 registered: tokenId=%d tx=%s", token_id, tx_hash.hex())
                return token_id

        log.warning("Could not extract tokenId from logs")
        return None

    except Exception as e:
        err = str(e)
        if "insufficient funds" in err.lower():
            log.error(
                "ERC-8004 register() could not be sent as a raw RPC transaction because "
                "Owner EOA has no CROSS for gas. Docs say identity gas is delegated, but "
                "this bot currently has no relayer endpoint and raw transactions still need gas: %s",
                e,
            )
        else:
            log.error("ERC-8004 register() error: %s", e)
        return None
