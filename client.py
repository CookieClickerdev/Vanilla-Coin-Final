import socket
import wmi
import hashlib
import blake3
import os
import json
import platform
import getpass
from datetime import datetime  # Fixed import - import datetime class directly

w = wmi.WMI() if platform.system() == "Windows" else None

HEADER = 64
PORT = 5050
FORMAT = 'utf-8'
DISCONNECT_MESSAGE = "!DISCONNECT"
SERVER = "127.0.0.1"  # Changed to localhost for testing
ADDR = (SERVER, PORT)
ID_CODE = "8e9acf8a6dd4ad6a5eed38bdd217a6e93d6b273ce74e886972c12dc58ceaea00"

# Hash test constants
VERIFY_HASH = 'test'
SINGLE_TEST = "4878ca0425c739fa427f7eda20fe845f6b2e46ba5fe2a14df5b1e32f50603215"
DOUBLE_TEST = "55beb65d3293549b07cf215978375cf674d82de8657775da6c0f697b4e6b5e0b"
TRIPLE_TEST = "1af8e96926a936cce32a1e304a068a3379968fd28c0843dcb08186adfaba1441"

class VanillaCoinClient:
    def __init__(self):
        self.client = None
        self.connected = False
        self.hardware_info = {}
        
    def get_hardware_info(self):
        """Get hardware information for different operating systems"""
        try:
            if platform.system() == "Windows" and w:
                # Get CPU info
                try:
                    cpu_info = w.Win32_Processor()[0]
                    self.hardware_info['cpu_id'] = getattr(cpu_info, 'ProcessorId', 'NO_CPU_ID')
                except:
                    self.hardware_info['cpu_id'] = 'WIN_CPU_ERROR'
                
                # Get disk info
                try:
                    disk_drives = w.Win32_DiskDrive()
                    if disk_drives and len(disk_drives) > 0:
                        disk_serial = getattr(disk_drives[0], 'SerialNumber', None)
                        self.hardware_info['disk_serial'] = disk_serial.strip() if disk_serial else "NO_DISK_SERIAL"
                    else:
                        self.hardware_info['disk_serial'] = "NO_DISK_FOUND"
                except:
                    self.hardware_info['disk_serial'] = 'WIN_DISK_ERROR'
                
                # Get RAM info
                try:
                    ram_modules = w.Win32_PhysicalMemory()
                    if ram_modules and len(ram_modules) > 0:
                        ram_serial = getattr(ram_modules[0], 'SerialNumber', None)
                        self.hardware_info['ram_id'] = ram_serial.strip() if ram_serial else "NO_RAM_SERIAL"
                    else:
                        self.hardware_info['ram_id'] = "NO_RAM_FOUND"
                except:
                    self.hardware_info['ram_id'] = 'WIN_RAM_ERROR'
                    
            elif platform.system() == "Linux":
                # Linux alternative methods
                try:
                    with open('/proc/cpuinfo', 'r') as f:
                        cpu_info = f.read()
                        # Extract processor serial or model
                        for line in cpu_info.split('\n'):
                            if 'processor' in line.lower() and ':' in line:
                                self.hardware_info['cpu_id'] = line.split(':')[1].strip()
                                break
                        else:
                            self.hardware_info['cpu_id'] = "LINUX_CPU_" + str(hash(cpu_info))[:16]
                except:
                    self.hardware_info['cpu_id'] = "LINUX_CPU_UNKNOWN"
                
                # Get disk info
                try:
                    import subprocess
                    result = subprocess.run(['lsblk', '-d', '-o', 'SERIAL'], capture_output=True, text=True)
                    if result.returncode == 0:
                        lines = result.stdout.strip().split('\n')
                        if len(lines) > 1:
                            self.hardware_info['disk_serial'] = lines[1].strip() or "LINUX_DISK_" + str(hash(lines[1]))[:16]
                        else:
                            self.hardware_info['disk_serial'] = "LINUX_DISK_UNKNOWN"
                    else:
                        self.hardware_info['disk_serial'] = "LINUX_DISK_UNKNOWN"
                except:
                    self.hardware_info['disk_serial'] = "LINUX_DISK_UNKNOWN"
                
                # RAM info
                try:
                    with open('/proc/meminfo', 'r') as f:
                        mem_info = f.read()
                        self.hardware_info['ram_id'] = "LINUX_RAM_" + str(hash(mem_info))[:16]
                except:
                    self.hardware_info['ram_id'] = "LINUX_RAM_UNKNOWN"
                    
            elif platform.system() == "Darwin":  # macOS
                import subprocess
                try:
                    # Get CPU info
                    result = subprocess.run(['system_profiler', 'SPHardwareDataType'], capture_output=True, text=True)
                    self.hardware_info['cpu_id'] = "MAC_CPU_" + str(hash(result.stdout))[:16]
                    
                    # Get disk info
                    result = subprocess.run(['system_profiler', 'SPStorageDataType'], capture_output=True, text=True)
                    self.hardware_info['disk_serial'] = "MAC_DISK_" + str(hash(result.stdout))[:16]
                    
                    # Get memory info
                    result = subprocess.run(['system_profiler', 'SPMemoryDataType'], capture_output=True, text=True)
                    self.hardware_info['ram_id'] = "MAC_RAM_" + str(hash(result.stdout))[:16]
                except:
                    self.hardware_info = {
                        'cpu_id': "MAC_CPU_UNKNOWN",
                        'disk_serial': "MAC_DISK_UNKNOWN", 
                        'ram_id': "MAC_RAM_UNKNOWN"
                    }
            else:
                # Fallback for unsupported systems
                self.hardware_info = {
                    'cpu_id': f"{platform.system()}_CPU_UNKNOWN",
                    'disk_serial': f"{platform.system()}_DISK_UNKNOWN",
                    'ram_id': f"{platform.system()}_RAM_UNKNOWN"
                }
                
            print(f"Hardware info collected for {platform.system()}")
            return True
            
        except Exception as e:
            print(f"Error collecting hardware info: {e}")
            return False

    def singleHash(self, content):
        return blake3.blake3(f"{content}".encode('utf-8')).hexdigest()

    def doubleHash(self, content):
        single = blake3.blake3(f"{content}".encode('utf-8')).hexdigest()
        double = blake3.blake3(f"{single}".encode('utf-8')).hexdigest()
        return double

    def tripleHash(self, content):
        single = blake3.blake3(f"{content}".encode('utf-8')).hexdigest()
        double = blake3.blake3(f"{single}".encode('utf-8')).hexdigest()
        triple = blake3.blake3(f"{double}".encode('utf-8')).hexdigest()
        return triple

    def verifyHash(self):
        """Verify hash functions are working correctly"""
        try:
            if self.singleHash(VERIFY_HASH) == SINGLE_TEST:
                print("✅ Single hash working")
            else:
                print("❌ SINGLE HASH TEST FAILED")
                return False
            
            if self.doubleHash(VERIFY_HASH) == DOUBLE_TEST:
                print("✅ Double hash working")
            else:
                print("❌ DOUBLE HASH TEST FAILED")
                return False
            
            if self.tripleHash(VERIFY_HASH) == TRIPLE_TEST:
                print("✅ Triple hash working")
            else:
                print("❌ TRIPLE HASH TEST FAILED")
                return False
                
            return True
        except Exception as e:
            print(f"❌ Hash verification failed: {e}")
            return False

    def connect_to_server(self):
        """Connect to the server"""
        try:
            self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.client.connect(ADDR)
            self.connected = True
            print(f"✅ Connected to server at {SERVER}:{PORT}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to server: {e}")
            return False

    def send_message(self, msg):
        """Send message to server with proper protocol"""
        if not self.connected:
            print("❌ Not connected to server")
            return None
            
        try:
            message = msg.encode(FORMAT)
            msg_length = len(message)
            send_length = str(msg_length).encode(FORMAT)
            send_length += b' ' * (HEADER - len(send_length))
            
            self.client.send(send_length)
            self.client.send(message)
            
            # Receive response
            response = self.client.recv(2048).decode(FORMAT)
            return response
        except Exception as e:
            print(f"❌ Failed to send message: {e}")
            return None

    def send_hardware_info(self):
        """Send hardware information to server"""
        if not self.hardware_info:
            print("❌ No hardware info available")
            return False
            
        print("📤 Sending hardware info...")
        
        try:
            # Send CPU ID
            response = self.send_message(f"{ID_CODE}CPU ID: {self.hardware_info['cpu_id']}")
            print(f"CPU: {response}")
            
            # Send Disk Serial
            response = self.send_message(f"{ID_CODE}Disk Serial Number: {self.hardware_info['disk_serial']}")
            print(f"Disk: {response}")
            
            # Send RAM ID
            response = self.send_message(f"{ID_CODE}RAM ID: {self.hardware_info['ram_id']}")
            print(f"RAM: {response}")
            
            print("✅ Hardware info sent successfully")
            return True
            
        except Exception as e:
            print(f"❌ Failed to send hardware info: {e}")
            return False

    def register_user(self):
        """Register a new user"""
        print("\n=== USER REGISTRATION ===")
        
        username = input("Enter username: ").strip()
        if not username:
            print("❌ Username cannot be empty")
            return False
        
        # Check if username already exists locally
        if os.path.exists(f"{username}_account.json"):
            print(f"❌ Username '{username}' is already taken!")
            print("💡 Please choose a different username")
            return False
        
        # Check with server if username is available
        print("🔍 Checking username availability...")
        try:
            check_msg = f"CHECK_USERNAME|{username}"
            response = self.send_message(check_msg)
            if response and "TAKEN" in response:
                print(f"❌ Username '{username}' is already taken on the server!")
                print("💡 Please choose a different username")
                return False
            elif response and "AVAILABLE" in response:
                print("✅ Username is available!")
        except Exception as e:
            print(f"⚠️  Could not check with server: {e}")
            print("🔄 Proceeding with local registration only")
            
        password = getpass.getpass("Enter password: ")
        if not password:
            print("❌ Password cannot be empty")
            return False
            
        confirm_password = getpass.getpass("Confirm password: ")
        if password != confirm_password:
            print("❌ Passwords don't match")
            return False
        
        print("\n🔐 Please write down these 5 security words - you'll need them if your hardware changes!")
        print("=" * 60)
        
        # Generate 5 random words (simplified for demo)
        import random
        simple_words = ['cat', 'dog', 'sun', 'moon', 'tree', 'rock', 'fish', 'bird', 'car', 'book',
                       'pen', 'cup', 'box', 'key', 'door', 'wall', 'roof', 'road', 'hill', 'lake',
                       'fire', 'water', 'wind', 'snow', 'rain', 'star', 'light', 'dark', 'hot', 'cold']
        
        word_list = random.sample(simple_words, 5)  # Changed from 20 to 5 words
        
        for i, word in enumerate(word_list, 1):
            print(f"{i:2d}. {word}")
            
        print("=" * 60)
        input("Press Enter when you've written down all words...")
        
        # Save user data locally for demo purposes
        user_data = {
            'username': username,
            'password_hash': self.doubleHash(password),
            'word_list': word_list,
            'hardware_info': self.hardware_info,
            'registered_at': datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # Fixed datetime call
        }
        
        try:
            with open(f"{username}_account.json", 'w') as f:
                json.dump(user_data, f, indent=2)
            print(f"✅ Account created and saved to {username}_account.json")
            print("⚠️  Keep your security words safe - you may need them if your hardware changes!")
            
            # Also try to register with server if possible
            try:
                # Include hardware info in registration
                hw_info = json.dumps(self.hardware_info)
                register_msg = f"REGISTER|{username}|{password}|{json.dumps(word_list)}|{hw_info}"
                response = self.send_message(register_msg)
                if response:
                    print(f"Server registration: {response}")
            except Exception as e:
                print(f"⚠️  Server registration failed: {e} (Account still saved locally)")
            
            return True
        except Exception as e:
            print(f"❌ Failed to save account: {e}")
            return False

    def login_user(self):
        """Login existing user"""
        print("\n=== USER LOGIN ===")
        
        username = input("Enter username: ").strip()
        if not username:
            print("❌ Username cannot be empty")
            return False
            
        password = getpass.getpass("Enter password: ")
        if not password:
            print("❌ Password cannot be empty")
            return False
        
        # First, check with server for hardware verification
        print("🔍 Verifying hardware with server...")
        try:
            # Include hardware info in login request
            hw_info = json.dumps(self.hardware_info)
            login_msg = f"LOGIN|{username}|{password}||{hw_info}"
            response = self.send_message(login_msg)

            # ---- PATCH START: stop on protocol/server errors instead of falling back locally ----
            if response and response.startswith("LOGIN_ERROR"):
                print(f"❌ {response}")
                return False
            # ---- PATCH END ----
            
            if response and "HARDWARE_MISMATCH" in response:
                print("⚠️  Hardware mismatch detected! Security words required.")
                print("\n🔐 Please enter your 5 security words:")
                input_words = []
                for i in range(5):  # Changed from 20 to 5 words
                    word = input(f"Word {i+1}: ").strip().lower()
                    input_words.append(word)
                
                # Send login with security words
                login_with_words_msg = f"LOGIN|{username}|{password}|{json.dumps(input_words)}|{hw_info}"
                response = self.send_message(login_with_words_msg)
                
            if response and "LOGIN_SUCCESS" in response:
                print("✅ We recognize This Device (LOGIN SUCCESSFUL)")
                return True
            elif response and "LOGIN_FAILED" in response:
                print(f"❌ {response}")
                return False
            else:
                print(f"⚠️  Unexpected server response: {response}")
                
        except Exception as e:
            print(f"⚠️  Server login failed: {e}")
        
        # Try local login as fallback
        try:
            with open(f"{username}_account.json", 'r') as f:
                user_data = json.load(f)
                
            # Verify password
            if self.doubleHash(password) != user_data['password_hash']:
                print("❌ Invalid password")
                return False
            
            # Check hardware changes (compare 2/3 components)
            stored_hw = user_data.get('hardware_info', {})
            current_hw = self.hardware_info
            
            matches = 0
            total_checks = 0
            
            for key in ['cpu_id', 'disk_serial', 'ram_id']:
                if key in stored_hw and key in current_hw:
                    total_checks += 1
                    if stored_hw[key] == current_hw[key]:
                        matches += 1
            
            # If less than 2/3 match, require security words
            if total_checks >= 3 and matches < 2:
                print("⚠️  Hardware changes detected! Security words required.")
                print("\n🔐 Please enter your 5 security words:")
                input_words = []
                for i in range(5):  # Changed from 20 to 5 words
                    word = input(f"Word {i+1}: ").strip().lower()
                    input_words.append(word)
                
                # Verify words
                stored_words = [word.lower() for word in user_data['word_list']]
                if input_words != stored_words:
                    print("❌ Security words don't match")
                    return False
                
                print("✅ Security words verified!")
            
            print("✅ Login successful!")
            return True
            
        except FileNotFoundError:
            print(f"❌ Account file not found for user: {username}")
            print("ℹ️  Please register first or check the username")
            return False
        except Exception as e:
            print(f"❌ Login failed: {e}")
            return False

    def show_help(self):
        """Show available commands"""
        print("\n🎮 INTERACTIVE MODE")
        print("Available commands:")
        print("  send <message>  - Send custom message to server")
        print("  hardware        - Send hardware info again")
        print("  register        - Register new user account")
        print("  login           - Login to existing account")
        print("  help            - Show this help")
        print("  quit            - Disconnect and exit")

    def interactive_mode(self):
        """Interactive client mode"""
        self.show_help()
        
        while self.connected:
            try:
                command = input("\n> ").strip().lower()
                
                if command == "quit" or command == "exit":
                    break
                elif command == "help":
                    self.show_help()
                elif command == "hardware":
                    self.send_hardware_info()
                elif command == "register":
                    self.register_user()
                    # Show menu again after registration
                    self.show_help()
                elif command == "login":
                    self.login_user()
                    # Show menu again after login
                    self.show_help()
                elif command.startswith("send "):
                    message = command[5:]  # Remove "send " prefix
                    response = self.send_message(message)
                    if response:
                        print(f"Server: {response}")
                elif command == "":
                    continue
                else:
                    print(f"❓ Unknown command: {command}")
                    print("Type 'help' for available commands")
                    
            except KeyboardInterrupt:
                print("\n🛑 Interrupted by user")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                break

    def disconnect(self):
        """Disconnect from server"""
        if self.connected:
            try:
                self.send_message(DISCONNECT_MESSAGE)
                print("📤 Disconnect message sent")
            except:
                pass
            
            try:
                self.client.close()
                print("✅ Disconnected from server")
            except:
                pass
            
            self.connected = False

def main():
    print("🪙 VANILLA COIN CLIENT v2.0")
    print("===========================")
    
    client = VanillaCoinClient()
    
    # Verify hash functions
    print("🔧 Verifying hash functions...")
    if not client.verifyHash():
        print("❌ Hash verification failed! Exiting.")
        return
    
    # Get hardware info
    print("🖥️  Collecting hardware information...")
    if not client.get_hardware_info():
        print("❌ Failed to collect hardware info! Exiting.")
        return
    
    # Connect to server
    print("🌐 Connecting to server...")
    if not client.connect_to_server():
        print("❌ Failed to connect to server! Make sure the server is running.")
        return
    
    try:
        # Send hardware info automatically
        client.send_hardware_info()
        
        # Enter interactive mode
        client.interactive_mode()
        
    except Exception as e:
        print(f"❌ Client error: {e}")
    finally:
        client.disconnect()
        print("👋 Goodbye!")

if __name__ == "__main__":
    main()
