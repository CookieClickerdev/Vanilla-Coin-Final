import socket
import threading
import hashlib
import pytz
import json
import mysql.connector
import blake3
import smtplib
import pandas as pd
import os
from mysql.connector import Error
from datetime import datetime, timedelta
from random_word import RandomWords
import time
import uuid

# ALL CONST VAR GO HERE
HEADER = 64
PORT = 5050
SERVER = "127.0.0.1"
ADDR = (SERVER, PORT)
FORMAT = 'utf-8'
DISCONNECT_MESSAGE = "!DISCONNECT"
SHUTDOWN_MESSAGE = "quit"
ENCODE_KEY = '@XM[2ui(#Y!ND1z[xq'
DECODE_KEY = '{+E%%)]XKSZ-w$SMS-'
ID_CODE = "8e9acf8a6dd4ad6a5eed38bdd217a6e93d6b273ce74e886972c12dc58ceaea00"

# Database configuration
DB_CONFIG = {
    'host': 'localhost',
    'user': 'vanillacoin',
    'password': 'Quietknot91$',
    'database': 'vanillacoin'
}

# Blockchain constants
BLOCK_TIME_TARGET = 10
DIFFICULTY_ADJUSTMENT_INTERVAL = 10
INITIAL_DIFFICULTY = 2
BLOCK_REWARD = 100
TRANSACTION_FEE = 0.01

# Hash test constants
VERIFY_HASH = 'test'
SINGLE_TEST = "4878ca0425c739fa427f7eda20fe845f6b2e46ba5fe2a14df5b1e32f50603215"
DOUBLE_TEST = "55beb65d3293549b07cf215978375cf674d82de8657775da6c0f697b4e6b5e0b"
TRIPLE_TEST = "1af8e96926a936cce32a1e304a068a3379968fd28c0843dcb08186adfaba1441"

# Global variables
server_running = True
connected_clients = []
blockchain = []
current_difficulty = INITIAL_DIFFICULTY
mydb = None
mycursor = None
pending_transactions = []

# Server setup
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(ADDR)

# Word list setup
r = RandomWords()

def setup_database():
    """Setup database and tables if they don't exist"""
    global mydb, mycursor
    
    try:
        connection_config = {
            'host': DB_CONFIG['host'],
            'user': DB_CONFIG['user'],
            'password': DB_CONFIG['password']
        }
        
        print(f"[DATABASE] Attempting to connect to MySQL server at {DB_CONFIG['host']} with user '{DB_CONFIG['user']}'...")
        
        mydb = mysql.connector.connect(**connection_config)
        mycursor = mydb.cursor()
        
        mycursor.execute("CREATE DATABASE IF NOT EXISTS vanillacoin")
        print("[DATABASE] Database 'vanillacoin' created or already exists")
        
        mycursor.close()
        mydb.close()
        
        print("[DATABASE] Connecting to vanillacoin database...")
        mydb = mysql.connector.connect(**DB_CONFIG)
        mycursor = mydb.cursor()
        
        print("[DATABASE] Creating tables...")
        
        mycursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_info (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(256) UNIQUE NOT NULL,
                password VARCHAR(256) NOT NULL,
                cpu_id VARCHAR(256),
                ram_id VARCHAR(256),
                motherboard_id VARCHAR(256),
                time_account_created VARCHAR(256),
                word_list JSON,
                balance DECIMAL(20,8) DEFAULT 0.00000000
            )
        """)
        
        mycursor.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                id INT AUTO_INCREMENT PRIMARY KEY,
                block_id INT UNIQUE NOT NULL,
                nonce VARCHAR(256) NOT NULL,
                previous_hash VARCHAR(256) NOT NULL,
                miner_id VARCHAR(256) NOT NULL,
                transactions TEXT,
                block_hash VARCHAR(256) NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                difficulty INT NOT NULL
            )
        """)
        
        mycursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                transaction_id VARCHAR(256) UNIQUE NOT NULL,
                from_username VARCHAR(256) NOT NULL,
                to_username VARCHAR(256) NOT NULL,
                amount DECIMAL(20,8) NOT NULL,
                fee DECIMAL(20,8) DEFAULT 0.00000000,
                status ENUM('pending', 'confirmed', 'failed') DEFAULT 'pending',
                block_id INT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (block_id) REFERENCES blocks(block_id) ON DELETE SET NULL
            )
        """)
        
        mycursor.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INT AUTO_INCREMENT PRIMARY KEY,
                address VARCHAR(256) UNIQUE NOT NULL,
                balance DECIMAL(20,8) DEFAULT 0.00000000,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)
        
        mydb.commit()
        print("[DATABASE] Database and tables setup complete")
        return True
        
    except Error as e:
        print(f"[DATABASE ERROR] {e}")
        print(f"[DATABASE ERROR] Error code: {e.errno}")
        if e.errno == 1045:
            print("[DATABASE ERROR] Access denied - Please check your MySQL username and password in DB_CONFIG")
        elif e.errno == 2003:
            print("[DATABASE ERROR] Can't connect to MySQL server - Make sure MySQL is running")
        
        return False
    except Exception as e:
        print(f"[DATABASE ERROR] Unexpected error: {e}")
        return False

def singleHash(content):
    return blake3.blake3(f"{content}".encode('utf-8')).hexdigest()
    
def doubleHash(content):
    single = blake3.blake3(f"{content}".encode('utf-8')).hexdigest()
    double = blake3.blake3(f"{single}".encode('utf-8')).hexdigest()
    return double

def tripleHash(content):
    single = blake3.blake3(f"{content}".encode('utf-8')).hexdigest()
    double = blake3.blake3(f"{single}".encode('utf-8')).hexdigest()
    triple = blake3.blake3(f"{double}".encode('utf-8')).hexdigest()
    return triple

def verifyHash():
    """Verify hash functions are working correctly"""
    try:
        if singleHash(VERIFY_HASH) == SINGLE_TEST:
            print("{HASH TEST} Single hash working.")
        else:
            print("{ERROR!!!} SINGLE HASH TEST FAILED.")
            return False
        
        if doubleHash(VERIFY_HASH) == DOUBLE_TEST:
            print("{HASH TEST} Double hash working.")
        else:
            print("{ERROR!!!} DOUBLE HASH TEST FAILED.")
            return False
        
        if tripleHash(VERIFY_HASH) == TRIPLE_TEST:
            print("{HASH TEST} Triple hash working.")
        else:
            print("{ERROR!!!} TRIPLE HASH TEST FAILED.")
            return False
            
        return True
    except Exception as e:
        print(f"[ERROR] Hash verification failed: {e}")
        return False

def getTime():
    pst_timezone = pytz.timezone("America/Los_Angeles")
    current_pst_time = datetime.now(pst_timezone)
    return current_pst_time.strftime("%Y-%m-%d %H:%M:%S")

def get_user_balance(username):
    """Get user's current balance from database"""
    if not mydb or not mycursor:
        return 0.0
        
    try:
        mycursor.execute("SELECT balance FROM customer_info WHERE username = %s", (username,))
        result = mycursor.fetchone()
        return float(result[0]) if result else 0.0
    except Exception as e:
        print(f"[BALANCE ERROR] {e}")
        return 0.0

def update_user_balance(username, new_balance):
    """Update user's balance in database"""
    if not mydb or not mycursor:
        return False
        
    try:
        mycursor.execute("UPDATE customer_info SET balance = %s WHERE username = %s", (new_balance, username))
        mydb.commit()
        return True
    except Exception as e:
        print(f"[BALANCE UPDATE ERROR] {e}")
        return False

def create_transaction(from_user, to_user, amount):
    """Create a new transaction"""
    if not mydb or not mycursor:
        return False, "No database connection"
    
    try:
        mycursor.execute("SELECT username FROM customer_info WHERE username IN (%s, %s)", (from_user, to_user))
        users = mycursor.fetchall()
        
        if len(users) != 2:
            return False, "One or both users not found"
        
        sender_balance = get_user_balance(from_user)
        fee = amount * TRANSACTION_FEE
        total_required = amount + fee
        
        if sender_balance < total_required:
            return False, f"Insufficient balance. Required: {total_required:.8f}, Available: {sender_balance:.8f}"
        
        transaction_id = str(uuid.uuid4())
        
        mycursor.execute("""
            INSERT INTO transactions (transaction_id, from_username, to_username, amount, fee, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
        """, (transaction_id, from_user, to_user, amount, fee))
        
        new_sender_balance = sender_balance - total_required
        receiver_balance = get_user_balance(to_user)
        new_receiver_balance = receiver_balance + amount
        
        update_user_balance(from_user, new_sender_balance)
        update_user_balance(to_user, new_receiver_balance)
        
        mycursor.execute("""
            UPDATE transactions SET status = 'confirmed' WHERE transaction_id = %s
        """, (transaction_id,))
        
        mydb.commit()
        
        print(f"[TRANSACTION] {from_user} sent {amount:.8f} VNC to {to_user} (fee: {fee:.8f})")
        return True, f"Transaction successful. ID: {transaction_id}"
        
    except Exception as e:
        print(f"[TRANSACTION ERROR] {e}")
        return False, str(e)

def get_transaction_history(username, limit=50):
    """Get transaction history for a user"""
    if not mydb or not mycursor:
        return []
        
    try:
        mycursor.execute("""
            SELECT transaction_id, from_username, to_username, amount, fee, status, timestamp
            FROM transactions 
            WHERE from_username = %s OR to_username = %s
            ORDER BY timestamp DESC
            LIMIT %s
        """, (username, username, limit))
        
        transactions = []
        for row in mycursor.fetchall():
            transaction = {
                'id': row[0],
                'from': row[1],
                'to': row[2],
                'amount': float(row[3]),
                'fee': float(row[4]),
                'status': row[5],
                'timestamp': row[6].strftime("%Y-%m-%d %H:%M:%S") if row[6] else None,
                'type': 'sent' if row[1] == username else 'received'
            }
            transactions.append(transaction)
            
        return transactions
        
    except Exception as e:
        print(f"[TRANSACTION HISTORY ERROR] {e}")
        return []

def load_blockchain():
    """Load blockchain from database"""
    global blockchain
    
    if not mydb or not mycursor:
        print("[BLOCKCHAIN ERROR] No database connection available")
        return
        
    try:
        mycursor.execute("SELECT * FROM blocks ORDER BY block_id")
        blocks = mycursor.fetchall()
        blockchain = []
        for block in blocks:
            blockchain.append({
                'block_id': block[1],
                'nonce': block[2],
                'previous_hash': block[3],
                'miner_id': block[4],
                'transactions': block[5],
                'block_hash': block[6],
                'timestamp': block[7],
                'difficulty': block[8]
            })
        print(f"[BLOCKCHAIN] Loaded {len(blockchain)} blocks")
    except Error as e:
        print(f"[BLOCKCHAIN ERROR] Failed to load blockchain: {e}")
        blockchain = []

def get_current_difficulty():
    """Calculate current mining difficulty"""
    global current_difficulty
    
    try:
        if len(blockchain) < DIFFICULTY_ADJUSTMENT_INTERVAL:
            return current_difficulty
        
        recent_blocks = blockchain[-DIFFICULTY_ADJUSTMENT_INTERVAL:]
        
        time_taken = 0
        for i in range(1, len(recent_blocks)):
            try:
                if isinstance(recent_blocks[i-1]['timestamp'], str):
                    prev_time = datetime.strptime(recent_blocks[i-1]['timestamp'], "%Y-%m-%d %H:%M:%S")
                else:
                    prev_time = recent_blocks[i-1]['timestamp']
                
                if isinstance(recent_blocks[i]['timestamp'], str):
                    curr_time = datetime.strptime(recent_blocks[i]['timestamp'], "%Y-%m-%d %H:%M:%S")
                else:
                    curr_time = recent_blocks[i]['timestamp']
                
                time_taken += (curr_time - prev_time).total_seconds()
                
            except (ValueError, TypeError, AttributeError) as e:
                print(f"[DIFFICULTY WARNING] Error processing timestamp: {e}")
                time_taken += BLOCK_TIME_TARGET
        
        avg_time = time_taken / (len(recent_blocks) - 1)
        
        if avg_time < BLOCK_TIME_TARGET:
            current_difficulty += 1
        elif avg_time > BLOCK_TIME_TARGET * 2:
            current_difficulty = max(1, current_difficulty - 1)
        
        print(f"[DIFFICULTY] Adjusted to {current_difficulty} (avg time: {avg_time}s)")
        return current_difficulty
        
    except (IndexError, KeyError, ZeroDivisionError) as e:
        print(f"[DIFFICULTY ERROR] Failed to calculate difficulty: {e}")
        return current_difficulty

def validate_block(block_data, block_hash):
    """Validate a mined block"""
    try:
        parts = block_data.split('.')
        if len(parts) < 5:
            return False, "Invalid block format"
        
        block_id = int(parts[0].split(': ')[1])
        nonce = parts[1].split(': ')[1]
        previous_hash = parts[2].split(': ')[1]
        miner_id = parts[3].split(': ')[1]
        transactions = parts[4].split(': ')[1]
        
        if mydb and mycursor:
            mycursor.execute("SELECT * FROM blocks WHERE block_id = %s", (block_id,))
            if mycursor.fetchone():
                return False, "Block already exists"
        
        calculated_hash = blake3.blake3(block_data.encode('utf-8')).hexdigest()
        if calculated_hash != block_hash:
            return False, "Hash mismatch"
        
        difficulty = get_current_difficulty()
        required_prefix = "0" * difficulty
        if not block_hash.startswith(required_prefix):
            return False, f"Hash doesn't meet difficulty requirement: {required_prefix}"
        
        if block_id > 1:
            if len(blockchain) == 0 or blockchain[-1]['block_hash'] != previous_hash:
                return False, "Invalid previous hash"
        
        return True, "Valid block"
        
    except Exception as e:
        return False, f"Validation error: {e}"

def store_block(block_data, block_hash, miner_id):
    """Store validated block in database and update balances"""
    if not mydb or not mycursor:
        print("[BLOCKCHAIN ERROR] No database connection available")
        return False
        
    try:
        parts = block_data.split('.')
        block_id = int(parts[0].split(': ')[1])
        nonce = parts[1].split(': ')[1]
        previous_hash = parts[2].split(': ')[1]
        transactions = parts[4].split(': ')[1]
        
        insert_query = """
            INSERT INTO blocks (block_id, nonce, previous_hash, miner_id, transactions, block_hash, timestamp, difficulty)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (block_id, nonce, previous_hash, miner_id, transactions, block_hash, datetime.now(), get_current_difficulty())
        mycursor.execute(insert_query, values)
        
        miner_balance = get_user_balance(miner_id)
        new_balance = miner_balance + BLOCK_REWARD
        update_user_balance(miner_id, new_balance)
        
        mydb.commit()
        
        blockchain.append({
            'block_id': block_id,
            'nonce': nonce,
            'previous_hash': previous_hash,
            'miner_id': miner_id,
            'transactions': transactions,
            'block_hash': block_hash,
            'timestamp': datetime.now(),
            'difficulty': get_current_difficulty()
        })
        
        print(f"[BLOCKCHAIN] Block {block_id} stored successfully. Miner {miner_id} rewarded {BLOCK_REWARD} coins")
        return True
        
    except Error as e:
        print(f"[BLOCKCHAIN ERROR] Failed to store block: {e}")
        return False

def broadcast_to_clients(message):
    """Broadcast message to all connected clients"""
    for client in connected_clients[:]:
        try:
            client.send(message.encode(FORMAT))
        except:
            connected_clients.remove(client)

def Add_User(username, password, cpu_id, ram_id, motherboard_id, word_list, hardware_info=None):
    """Add user to database with proper word list storage and hardware info"""
    if not mydb or not mycursor:
        return False, "No database connection"
        
    try:
        if hardware_info:
            cpu_id = hardware_info.get('cpu_id', '')
            ram_id = hardware_info.get('ram_id', '')
            motherboard_id = hardware_info.get('disk_serial', '')

        mycursor.execute("SELECT 1 FROM customer_info WHERE username = %s", (username,))
        if mycursor.fetchone():
            return False, "Username already exists"
        
        hashed_words = [singleHash(word) for word in (word_list or [])]
        
        insert_query = """
            INSERT INTO customer_info (
                username, password, cpu_id, ram_id, motherboard_id, time_account_created, word_list, balance
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        values = (
            username,
            doubleHash(password),
            singleHash(cpu_id or ''),
            singleHash(ram_id or ''),
            singleHash(motherboard_id or ''),
            getTime(),
            json.dumps(hashed_words),
            0.00000000
        )
        
        mycursor.execute(insert_query, values)
        mydb.commit()
        print(f"[USER] Added user: {username} to database. ID: {mycursor.lastrowid}")
        return True, "User created successfully"
        
    except Error as e:
        print(f"[USER ERROR] {e}")
        return False, str(e)

def verify_hardware_match(username, current_hardware):
    """Check if current hardware matches stored hardware (2/3 rule)"""
    if not mydb or not mycursor:
        return True
        
    try:
        mycursor.execute("SELECT cpu_id, ram_id, motherboard_id FROM customer_info WHERE username = %s", (username,))
        result = mycursor.fetchone()
        
        if not result:
            return False
            
        stored_cpu, stored_ram, stored_motherboard = result
        
        matches = 0
        total_checks = 3
        
        if singleHash(current_hardware.get('cpu_id', '')) == stored_cpu:
            matches += 1
        if singleHash(current_hardware.get('ram_id', '')) == stored_ram:
            matches += 1
        if singleHash(current_hardware.get('disk_serial', '')) == stored_motherboard:
            matches += 1
            
        return matches >= 2
        
    except Exception as e:
        print(f"[HARDWARE CHECK ERROR] {e}")
        return True

def verify_user_login(username, password, word_list=None, hardware_info=None):
    """Verify user login with username, password, and optional word list"""
    if not mydb or not mycursor:
        return False, "No database connection"
        
    try:
        mycursor.execute("SELECT password, word_list, cpu_id, ram_id, motherboard_id FROM customer_info WHERE username = %s", (username,))
        result = mycursor.fetchone()
        
        if not result:
            return False, "User not found"
        
        stored_password, stored_word_list, stored_cpu, stored_ram, stored_motherboard = result
        
        if doubleHash(password) != stored_password:
            return False, "Invalid password"
        
        if hardware_info:
            hardware_matches = verify_hardware_match(username, hardware_info)
            
            if not hardware_matches:
                if not word_list:
                    return False, "HARDWARE_MISMATCH"
                
                hashed_input_words = [singleHash(word) for word in word_list]
                stored_words = json.loads(stored_word_list)
                
                if hashed_input_words != stored_words:
                    return False, "Invalid security words"
                
                print(f"[LOGIN] User {username} authenticated with security words due to hardware changes")
            else:
                print(f"[LOGIN] User {username} authenticated with hardware verification")
        
        return True, "Login successful"
        
    except Exception as e:
        print(f"[LOGIN ERROR] {e}")
        return False, str(e)

def generate_word_security():
    """Generate 5 unique random words for security"""
    word_list = []
    attempts = 0
    max_attempts = 100
    
    while len(word_list) < 5 and attempts < max_attempts:
        try:
            word = r.get_random_word()
            if word and len(word) <= 6 and word not in word_list:
                word_list.append(word)
        except:
            pass
        attempts += 1
    
    if len(word_list) < 5:
        fallback_words = ['cat', 'dog', 'sun', 'moon', 'tree', 'rock', 'fish', 'bird', 'car', 'book']
        word_list.extend(fallback_words[:5-len(word_list)])
    
    return word_list[:5]

# ---- FIXED: send_response helper ----
def send_response(conn, response_text):
    """Send a properly formatted response with header"""
    try:
        message = response_text.encode(FORMAT)
        msg_length = len(message)
        send_length = str(msg_length).encode(FORMAT)
        send_length += b' ' * (HEADER - len(send_length))
        
        conn.sendall(send_length)
        conn.sendall(message)
    except Exception as e:
        print(f"[SEND RESPONSE ERROR] {e}")

def shutdown_server():
    """Shutdown server when quit command is entered"""
    global server_running
    while server_running:
        try:
            command = input()
            if command.strip().lower() == SHUTDOWN_MESSAGE:
                server_running = False
                print("[SHUTDOWN] Server is shutting down...")
                break
        except:
            break

def handle_client(conn, addr):
    """Handle individual client connections"""
    print(f"[NEW CONNECTION] {addr} connected.")
    connected_clients.append(conn)
    connected = True
    
    try:
        while connected and server_running:
            msg_length = conn.recv(HEADER).decode(FORMAT)
            
            if not msg_length:
                break
                
            try:
                msg_length = int(msg_length.strip())
            except ValueError:
                print(f"[PROTOCOL ERROR] {addr} Invalid header: {msg_length[:20]}...")
                break
            
            msg = conn.recv(msg_length).decode(FORMAT)
            
            if msg == DISCONNECT_MESSAGE:
                connected = False
                break
            
            print(f"[{addr}] {msg}")
            
            # Handle balance requests
            if "GET_BALANCE|" in msg:
                try:
                    username = msg.split("GET_BALANCE|")[1].strip()
                    balance = get_user_balance(username)
                    response = json.dumps({"balance": f"{balance:.8f}"})
                    send_response(conn, response)
                    
                except Exception as e:
                    error_msg = f"BALANCE_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # Handle transaction requests
            elif "SEND_TRANSACTION|" in msg:
                try:
                    payload = msg.split("SEND_TRANSACTION|", 1)[1]
                    parts = payload.split("|")
                    
                    if len(parts) >= 3:
                        from_user = parts[0]
                        to_user = parts[1]
                        amount = float(parts[2])
                        
                        success, message = create_transaction(from_user, to_user, amount)
                        
                        if success:
                            response = f"SEND_SUCCESS: {message}"
                            print(f"[{addr}] Transaction successful: {from_user} -> {to_user} : {amount} VNC")
                        else:
                            response = f"TRANSACTION_FAILED: {message}"
                            print(f"[{addr}] Transaction failed: {message}")
                        
                        send_response(conn, response)
                    else:
                        send_response(conn, "TRANSACTION_FAILED: Invalid transaction data")
                        
                except Exception as e:
                    error_msg = f"TRANSACTION_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # Handle transaction history requests
            elif "GET_HISTORY|" in msg:
                try:
                    username = msg.split("GET_HISTORY|")[1].strip()
                    history = get_transaction_history(username)
                    response = json.dumps(history)
                    send_response(conn, response)
                    
                except Exception as e:
                    error_msg = f"HISTORY_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # ---- NEW: Handle MINE command ----
            elif "MINE|" in msg:
                try:
                    payload = msg.split("MINE|", 1)[1]
                    parts = payload.split("|")
                    
                    if len(parts) >= 2:
                        username = parts[0]
                        seconds = int(parts[1])
                        
                        # Simulate instant mining (give reward immediately)
                        # In production, this would trigger actual mining
                        balance = get_user_balance(username)
                        new_balance = balance + BLOCK_REWARD
                        update_user_balance(username, new_balance)
                        
                        response = f"MINE_SUCCESS: Mined {BLOCK_REWARD} VNC for {username}"
                        print(f"[MINING] {username} mined {BLOCK_REWARD} VNC (simulated)")
                        send_response(conn, response)
                    else:
                        send_response(conn, "MINE_FAILED: Invalid mining data")
                        
                except Exception as e:
                    error_msg = f"MINE_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # ---- NEW: Handle AIR_DROP command ----
            elif "AIR_DROP|" in msg:
                try:
                    payload = msg.split("AIR_DROP|", 1)[1]
                    parts = payload.split("|")
                    
                    if len(parts) >= 2:
                        to_user = parts[0]
                        amount = float(parts[1])
                        
                        # Give coins directly (admin airdrop)
                        balance = get_user_balance(to_user)
                        new_balance = balance + amount
                        update_user_balance(to_user, new_balance)
                        
                        response = f"AIR_DROP_SUCCESS: {amount} VNC airdropped to {to_user}"
                        print(f"[AIRDROP] {amount} VNC airdropped to {to_user}")
                        send_response(conn, response)
                    else:
                        send_response(conn, "AIR_DROP_FAILED: Invalid airdrop data")
                        
                except Exception as e:
                    error_msg = f"AIR_DROP_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # Handle username availability check
            elif "CHECK_USERNAME|" in msg:
                try:
                    username = msg.split("CHECK_USERNAME|")[1].strip()
                    print(f"[{addr}] Checking username availability: {username}")
                    
                    if mydb and mycursor:
                        mycursor.execute("SELECT username FROM customer_info WHERE username = %s", (username,))
                        result = mycursor.fetchone()
                        if result:
                            response = f"USERNAME_TAKEN: {username} is already registered"
                        else:
                            response = f"USERNAME_AVAILABLE: {username} is available"
                    else:
                        if os.path.exists(f"{username}_account.json"):
                            response = f"USERNAME_TAKEN: {username} is already registered"
                        else:
                            response = f"USERNAME_AVAILABLE: {username} is available"
                    
                    send_response(conn, response)
                    
                except Exception as e:
                    error_msg = f"USERNAME_CHECK_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # Handle user registration
            elif "REGISTER|" in msg:
                try:
                    payload = msg.split("REGISTER|", 1)[1]
                    parts = payload.split("|")

                    if len(parts) >= 3:
                        username = parts[0]
                        password = parts[1]

                        try:
                            word_list = json.loads(parts[2])
                        except Exception as e:
                            send_response(conn, "REGISTRATION_FAILED: Invalid word list JSON")
                            print(f"[ERROR] Registration word list parse: {e}")
                            continue

                        hardware_info = None
                        if len(parts) >= 4 and parts[3].strip():
                            try:
                                hardware_info = json.loads(parts[3])
                            except Exception as e:
                                print(f"[REGISTER PARSE] hardware JSON error: {e}")

                        success, message = Add_User(
                            username, password, None, None, None, word_list, hardware_info=hardware_info
                        )

                        if success:
                            response = f"REGISTRATION_SUCCESS: {message}"
                            print(f"[{addr}] User {username} registered successfully")
                        else:
                            response = f"REGISTRATION_FAILED: {message}"
                            print(f"[{addr}] Registration failed for {username}: {message}")

                        send_response(conn, response)
                    else:
                        send_response(conn, "REGISTRATION_FAILED: Invalid registration data")

                except Exception as e:
                    error_msg = f"REGISTRATION_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] Registration error: {e}")
            
            # Handle user login
            elif "LOGIN|" in msg:
                try:
                    payload = msg.split("LOGIN|", 1)[1]
                    parts = payload.split("|")

                    if len(parts) >= 2:
                        username = parts[0]
                        password = parts[1]

                        word_list = None
                        hardware_info = None

                        if len(parts) >= 3 and parts[2].strip():
                            try:
                                word_list = json.loads(parts[2])
                            except Exception as e:
                                print(f"[LOGIN PARSE] word_list JSON error: {e}")

                        if len(parts) >= 4 and parts[3].strip():
                            try:
                                hardware_info = json.loads(parts[3])
                            except Exception as e:
                                print(f"[LOGIN PARSE] hardware JSON error: {e}")

                        success, message = verify_user_login(username, password, word_list, hardware_info)

                        if success:
                            response = f"LOGIN_SUCCESS: {message}"
                            print(f"[{addr}] User {username} logged in successfully")
                        else:
                            response = f"LOGIN_FAILED: {message}"
                            print(f"[{addr}] Login failed for {username}: {message}")

                        send_response(conn, response)
                    else:
                        send_response(conn, "LOGIN_FAILED: Invalid login data")

                except Exception as e:
                    error_msg = f"LOGIN_ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            # Handle hardware ID messages
            elif f"{ID_CODE}CPU ID:" in msg:
                cpu_id = msg.split('CPU ID: ')[1]
                print(f"[{addr}] CPU ID RECEIVED: {cpu_id}")
                send_response(conn, "CPU INFO RECEIVED")
            
            elif f"{ID_CODE}Disk Serial Number:" in msg:
                disk_serial = msg.split('Disk Serial Number: ')[1]
                print(f"[{addr}] DISK SERIAL NUMBER RECEIVED: {disk_serial}")
                send_response(conn, "DISK INFO RECEIVED")
            
            elif f"{ID_CODE}RAM ID:" in msg:
                ram_id = msg.split('RAM ID: ')[1]
                print(f"[{addr}] RAM ID RECEIVED: {ram_id}")
                send_response(conn, "RAM INFO RECEIVED")
            
            # Handle mined blocks
            elif "|||" in msg:
                try:
                    block_data, block_hash = msg.split("|||")
                    
                    miner_id = block_data.split("MinerPublicID: ")[1].split(".")[0]
                    
                    is_valid, validation_msg = validate_block(block_data, block_hash)
                    
                    if is_valid:
                        if store_block(block_data, block_hash, miner_id):
                            response = f"BLOCK ACCEPTED: {validation_msg}"
                            broadcast_to_clients(f"NEW_BLOCK|||{msg}")
                        else:
                            response = "BLOCK REJECTED: Storage failed"
                    else:
                        response = f"BLOCK REJECTED: {validation_msg}"
                    
                    send_response(conn, response)
                    print(f"[MINING] {response}")
                    
                except Exception as e:
                    error_msg = f"BLOCK PROCESSING ERROR: {e}"
                    send_response(conn, error_msg)
                    print(f"[ERROR] {error_msg}")
            
            else:
                send_response(conn, f"MSG received: {msg}")
                    
    except Exception as e:
        print(f"[CONNECTION ERROR] {addr}: {e}")
    finally:
        if conn in connected_clients:
            connected_clients.remove(conn)
        conn.close()
        print(f"[DISCONNECTED] {addr} disconnected.")

def start():
    """Start the server"""
    print("[STARTING] Server is starting...")
    server.listen()
    print(f"[LISTENING] Server is listening on {SERVER}:{PORT}")
    print(f"[INFO] Type '{SHUTDOWN_MESSAGE}' and press Enter to stop the server")
    
    while server_running:
        try:
            server.settimeout(1.0)
            conn, addr = server.accept()
            thread = threading.Thread(target=handle_client, args=(conn, addr))
            thread.daemon = True
            thread.start()
            print(f"[ACTIVE CONNECTIONS] {threading.active_count() - 2}")
        except socket.timeout:
            continue
        except Exception as e:
            if server_running:
                print(f"[SERVER ERROR] {e}")
            break

def main():
    """Main function to initialize and start server"""
    global server_running
    
    print("=== VANILLA COIN BLOCKCHAIN SERVER v3.0 ===")
    print("Starting VanillaCoin blockchain server with transaction support...")
    
    print("\nSetting up database...")
    if not setup_database():
        print("Database setup failed! Server will run with limited functionality.")
        print("Blocks will not be persisted and user accounts will not work.")
        print("Please fix your MySQL connection and restart the server.")
        
        response = input("\nDo you want to continue anyway? (y/n): ").strip().lower()
        if response != 'y' and response != 'yes':
            print("Exiting server...")
            return
    
    print("\nVerifying hash functions...")
    if not verifyHash():
        print("Hash verification failed! Exiting.")
        return
    
    print("\nLoading blockchain...")
    load_blockchain()
    
    server_thread = threading.Thread(target=start)
    server_thread.daemon = True
    server_thread.start()
    
    shutdown_thread = threading.Thread(target=shutdown_server)
    shutdown_thread.daemon = True
    shutdown_thread.start()
    
    try:
        while server_running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Server interrupted by user")
        server_running = False
    finally:
        if mydb:
            try:
                mycursor.close()
                mydb.close()
                print("[DATABASE] Database connection closed")
            except:
                pass
    
    print("[SHUTDOWN] Server stopped")
    print("Goodbye!")

if __name__ == "__main__":
    main()