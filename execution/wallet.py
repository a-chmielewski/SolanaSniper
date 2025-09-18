import base58
import json
import requests
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction
from config import WALLET_PRIVATE_KEY

SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

class SolanaWallet:
    def __init__(self, private_key_str=None):
        if private_key_str and private_key_str != "your-wallet-private-key":
            try:
                private_key_bytes = base58.b58decode(private_key_str)
                self.keypair = Keypair.from_bytes(private_key_bytes)
                self.public_key = self.keypair.pubkey()
            except Exception as e:
                print(f"Invalid private key: {e}")
                self.keypair = None
                self.public_key = None
        else:
            self.keypair = None
            self.public_key = None
    
    def get_address(self):
        return str(self.public_key) if self.public_key else None
    
    def get_sol_balance(self):
        if not self.public_key:
            return 0
        
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [str(self.public_key)]
            }
            response = requests.post(SOLANA_RPC_URL, json=payload, timeout=10)
            result = response.json()
            
            if 'result' in result:
                lamports = result['result']['value']
                return lamports / 1e9  # Convert lamports to SOL
            return 0
        except Exception as e:
            print(f"Error getting balance: {e}")
            return 0
    
    def get_token_balance(self, token_mint):
        if not self.public_key:
            return 0
        
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    str(self.public_key),
                    {"mint": token_mint},
                    {"encoding": "jsonParsed"}
                ]
            }
            response = requests.post(SOLANA_RPC_URL, json=payload, timeout=10)
            result = response.json()
            
            if 'result' in result and result['result']['value']:
                for account in result['result']['value']:
                    token_amount = account['account']['data']['parsed']['info']['tokenAmount']
                    return float(token_amount['uiAmount'] or 0)
            return 0
        except Exception as e:
            print(f"Error getting token balance: {e}")
            return 0
    
    def sign_transaction(self, transaction_data):
        if not self.keypair:
            print("No keypair available for signing")
            return None
        
        try:
            if isinstance(transaction_data, str):
                # Try base64 first (Jupiter default), fallback to base58
                try:
                    import base64
                    tx_bytes = base64.b64decode(transaction_data)
                except Exception:
                    tx_bytes = base58.b58decode(transaction_data)
                
                transaction = VersionedTransaction.from_bytes(tx_bytes)
            else:
                transaction = transaction_data
            
            # Sign transaction
            transaction.sign([self.keypair])
            return transaction
        except Exception as e:
            print(f"Error signing transaction: {e}")
            return None
    
    def send_transaction(self, signed_transaction):
        if not signed_transaction:
            return None
        
        try:
            # Serialize signed transaction
            tx_bytes = bytes(signed_transaction)
            tx_base64 = base58.b58encode(tx_bytes).decode('utf-8')
            
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    tx_base64,
                    {
                        "encoding": "base58",
                        "skipPreflight": False,
                        "preflightCommitment": "processed"
                    }
                ]
            }
            
            response = requests.post(SOLANA_RPC_URL, json=payload, timeout=30)
            result = response.json()
            
            if 'result' in result:
                return {"tx_id": result['result'], "status": "sent"}
            else:
                error = result.get('error', 'Unknown error')
                print(f"Transaction failed: {error}")
                return {"error": error}
                
        except Exception as e:
            print(f"Error sending transaction: {e}")
            return {"error": str(e)}
    
    def get_transaction_status(self, tx_id):
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignatureStatuses",
                "params": [[tx_id], {"searchTransactionHistory": True}]
            }
            
            response = requests.post(SOLANA_RPC_URL, json=payload, timeout=10)
            result = response.json()
            
            if 'result' in result and result['result']['value']:
                status_info = result['result']['value'][0]
                if status_info:
                    if status_info.get('confirmationStatus') == 'finalized':
                        return "confirmed"
                    elif status_info.get('err'):
                        return "failed"
                    else:
                        return "pending"
            return "unknown"
        except Exception as e:
            print(f"Error getting transaction status: {e}")
            return "error"

# Global wallet instance
def load_wallet(private_key=None):
    key = private_key or WALLET_PRIVATE_KEY
    return SolanaWallet(key)

def get_balance(wallet):
    if isinstance(wallet, SolanaWallet):
        return wallet.get_sol_balance()
    return 0

def sign_transaction(wallet, tx_data):
    if isinstance(wallet, SolanaWallet):
        return wallet.sign_transaction(tx_data)
    return None

def send_transaction(wallet, signed_tx):
    if isinstance(wallet, SolanaWallet):
        return wallet.send_transaction(signed_tx)
    return None
