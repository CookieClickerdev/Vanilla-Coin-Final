use blake3;
use std::io::{self, BufRead, BufReader, Write};
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH, Duration, Instant};
use rand;
use std::net::TcpStream;
use serde_json;
use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Clone, Debug)]
struct Block {
    id: u32,
    nonce: String,
    previous_hash: String,
    miner_public_id: String,
    transactions: String,
    hash: String,
    timestamp: u64,
    difficulty: u32,
}

#[derive(Serialize, Deserialize)]
struct Blockchain {
    blocks: Vec<Block>,
    difficulty: u32,
    last_block_time: u64,
}

impl Blockchain {
    fn new() -> Self {
        Blockchain {
            blocks: Vec::new(),
            difficulty: 2, // Start with "00"
            last_block_time: Self::current_timestamp(),
        }
    }

    fn current_timestamp() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }

    fn load_from_file() -> Self {
        match std::fs::read_to_string("blockchain.json") {
            Ok(data) => {
                match serde_json::from_str(&data) {
                    Ok(blockchain) => blockchain,
                    Err(_) => {
                        println!("Failed to parse blockchain.json, creating new blockchain");
                        Self::new()
                    }
                }
            }
            Err(_) => {
                println!("No existing blockchain found, creating new one");
                Self::new()
            }
        }
    }

    fn save_to_file(&self) {
        match serde_json::to_string_pretty(self) {
            Ok(data) => {
                if let Err(e) = std::fs::write("blockchain.json", data) {
                    println!("Failed to save blockchain: {}", e);
                }
            }
            Err(e) => println!("Failed to serialize blockchain: {}", e),
        }
    }

    fn add_block(&mut self, block: Block) {
        self.blocks.push(block);
        self.last_block_time = Self::current_timestamp();
        self.adjust_difficulty();
        self.save_to_file();
    }

    fn get_last_block(&self) -> Option<&Block> {
        self.blocks.last()
    }

    fn get_next_block_id(&self) -> u32 {
        match self.get_last_block() {
            Some(block) => block.id + 1,
            None => 1,
        }
    }

    fn get_previous_hash(&self) -> String {
        match self.get_last_block() {
            Some(block) => block.hash.clone(),
            None => "0000000000000000000000000000000000000000000000000000000000000000".to_string(),
        }
    }

    fn adjust_difficulty(&mut self) {
        const TARGET_BLOCK_TIME: u64 = 10; // 10 seconds
        const DIFFICULTY_ADJUSTMENT_INTERVAL: usize = 10;

        if self.blocks.len() % DIFFICULTY_ADJUSTMENT_INTERVAL == 0 && self.blocks.len() > 0 {
            let current_time = Self::current_timestamp();
            let time_taken = current_time - self.last_block_time;

            if time_taken < TARGET_BLOCK_TIME {
                self.difficulty += 1;
                println!("Difficulty increased to: {}", self.difficulty);
            } else if time_taken > TARGET_BLOCK_TIME * 2 && self.difficulty > 1 {
                self.difficulty -= 1;
                println!("Difficulty decreased to: {}", self.difficulty);
            }
        }
    }

    #[allow(dead_code)]
    fn validate_block(&self, block: &Block) -> bool {
        // Check if hash starts with required number of zeros
        let required_prefix = "0".repeat(self.difficulty as usize);
        if !block.hash.starts_with(&required_prefix) {
            return false;
        }

        // Verify hash is correct
        let block_string = Self::create_block_template(
            block.id,
            &block.nonce,
            &block.previous_hash,
            &block.miner_public_id,
            &block.transactions,
        );

        let calculated_hash = blake3::hash(block_string.as_bytes()).to_hex().to_string();
        block.hash == calculated_hash
    }

    fn create_block_template(id: u32, nonce: &str, previous_hash: &str, miner_public_id: &str, transactions: &str) -> String {
        format!(
            "ID: {}.\
             Nonce: {}.\
             PreviousHash: {}.\
             MinerPublicID: {}.\
             Transactions: {}.",
            id, nonce, previous_hash, miner_public_id, transactions
        )
    }
}

struct Miner {
    blockchain: Arc<std::sync::Mutex<Blockchain>>,
    miner_id: String,
    balance: f64,
}

impl Miner {
    fn new(miner_id: String) -> Self {
        let blockchain = Arc::new(std::sync::Mutex::new(Blockchain::load_from_file()));
        
        Miner {
            blockchain,
            miner_id,
            balance: 0.0,
        }
    }

    fn mine_block(&mut self, should_quit: Arc<AtomicBool>, show_logs: Arc<AtomicBool>, transactions: &str) -> Option<Block> {
        let mut attempt = 0u64;
        let start_time = Instant::now();
        
        let (block_id, previous_hash, difficulty) = {
            let blockchain = self.blockchain.lock().unwrap();
            (
                blockchain.get_next_block_id(),
                blockchain.get_previous_hash(),
                blockchain.difficulty,
            )
        };

        let required_prefix = "0".repeat(difficulty as usize);
        let full_transactions = format!("{}+100, {}", self.miner_id, transactions);

        loop {
            if should_quit.load(Ordering::Relaxed) {
                println!("Mining stopped by user request at attempt {}", attempt);
                return None;
            }

            // Generate random nonce
            let a = rand::random::<u128>();
            let b = rand::random::<u128>();
            let c = rand::random::<u128>();
            let nonce = format!("{}{}{}", a, b, c);

            let block_template = Blockchain::create_block_template(
                block_id,
                &nonce,
                &previous_hash,
                &self.miner_id,
                &full_transactions,
            );

            let hash = blake3::hash(block_template.as_bytes()).to_hex().to_string();

            if hash.starts_with(&required_prefix) {
                let elapsed = start_time.elapsed();
                println!("🎉 SUCCESSFULLY MINED BLOCK {}!", block_id);
                println!("   Hash: {}", hash);
                println!("   Attempts: {}", attempt);
                println!("   Time: {:.2} seconds", elapsed.as_secs_f64());
                println!("   Difficulty: {} ({})", difficulty, required_prefix);

                let block = Block {
                    id: block_id,
                    nonce,
                    previous_hash,
                    miner_public_id: self.miner_id.clone(),
                    transactions: full_transactions,
                    hash,
                    timestamp: Blockchain::current_timestamp(),
                    difficulty,
                };

                // Add to local blockchain
                {
                    let mut blockchain = self.blockchain.lock().unwrap();
                    blockchain.add_block(block.clone());
                }

                self.balance += 100.0; // Block reward
                return Some(block);
            }

            if show_logs.load(Ordering::Relaxed) {
                if attempt % 10000 == 0 {
                    println!("Attempt: {} | Hash: {}...", attempt, &hash[..10]);
                }
            }

            attempt += 1;
        }
    }

    fn send_block_to_server(&self, block: &Block) -> Result<String, Box<dyn std::error::Error>> {
        let block_data = Blockchain::create_block_template(
            block.id,
            &block.nonce,
            &block.previous_hash,
            &block.miner_public_id,
            &block.transactions,
        );

        let message = format!("{}|||{}", block_data, block.hash);
        
        match TcpStream::connect("127.0.0.1:5050") {
            Ok(mut stream) => {
                // Send message length header (64 bytes)
                let msg_bytes = message.as_bytes();
                let length = msg_bytes.len();
                let mut header = format!("{}", length).into_bytes();
                header.resize(64, b' '); // Pad to 64 bytes
                
                stream.write_all(&header)?;
                stream.write_all(msg_bytes)?;
                stream.flush()?;

                // Read response
                let mut response = String::new();
                let mut reader = BufReader::new(&stream);
                reader.read_line(&mut response)?;

                println!("Server response: {}", response.trim());
                Ok(response)
            }
            Err(e) => {
                println!("Failed to connect to server: {}", e);
                Err(Box::new(e))
            }
        }
    }

    fn get_balance(&self) -> f64 {
        self.balance
    }

    fn get_blockchain_info(&self) -> (usize, u32) {
        let blockchain = self.blockchain.lock().unwrap();
        (blockchain.blocks.len(), blockchain.difficulty)
    }
}

fn main() {
    println!("🚀 VANILLA COIN BLOCKCHAIN MINER v2.0");
    println!("=====================================");
    
    let should_quit = Arc::new(AtomicBool::new(false));
    let show_logs = Arc::new(AtomicBool::new(false));
    
    // Get miner ID from user
    println!("Enter your miner ID (username): ");
    let mut miner_id = String::new();
    io::stdin().read_line(&mut miner_id).unwrap();
    let miner_id = miner_id.trim().to_string();
    
    let miner = Miner::new(miner_id.clone());
    
    // Start mining in a separate thread
    let mining_thread = {
        let should_quit = Arc::clone(&should_quit);
        let show_logs = Arc::clone(&show_logs);
        let mut miner_clone = Miner::new(miner_id.clone());
        
        thread::spawn(move || {
            let mut block_count = 0;
            
            while !should_quit.load(Ordering::Relaxed) {
                let transactions = format!("Block_{}_transactions", block_count);
                
                if let Some(block) = miner_clone.mine_block(
                    Arc::clone(&should_quit),
                    Arc::clone(&show_logs),
                    &transactions
                ) {
                    block_count += 1;
                    
                    // Try to send to server
                    match miner_clone.send_block_to_server(&block) {
                        Ok(response) => {
                            if response.contains("ACCEPTED") {
                                println!("✅ Block accepted by network!");
                            } else {
                                println!("❌ Block rejected: {}", response);
                            }
                        }
                        Err(e) => {
                            println!("⚠️  Failed to send to server: {}", e);
                            println!("Block saved locally only");
                        }
                    }
                    
                    let (chain_length, difficulty) = miner_clone.get_blockchain_info();
                    println!("💰 Balance: {} coins | Blocks mined: {} | Chain length: {} | Difficulty: {}", 
                             miner_clone.get_balance(), block_count, chain_length, difficulty);
                } else {
                    break; // Mining was stopped
                }
                
                // Small delay between mining attempts
                thread::sleep(Duration::from_millis(100));
            }
            
            format!("Mining stopped. Total blocks mined: {}", block_count)
        })
    };
    
    // Handle user input in main thread
    println!("\n🎮 MINING CONTROLS:");
    println!("- Type '1' to toggle mining logs on/off");
    println!("- Type 'status' to show current status");
    println!("- Type 'balance' to show your balance");
    println!("- Type 'blocks' to show blockchain info");
    println!("- Type 'q' and press Enter to stop mining");
    println!("\nMining started! 💎⛏️");
    
    loop {
        let mut input = String::new();
        match io::stdin().read_line(&mut input) {
            Ok(_) => {
                let input = input.trim().to_lowercase();
                match input.as_str() {
                    "q" | "quit" | "exit" => {
                        println!("🛑 Stopping mining process...");
                        should_quit.store(true, Ordering::Relaxed);
                        break;
                    }
                    "1" | "logs" => {
                        let current_state = show_logs.load(Ordering::Relaxed);
                        show_logs.store(!current_state, Ordering::Relaxed);
                        if !current_state {
                            println!("📊 Mining logs enabled!");
                        } else {
                            println!("🔇 Mining logs disabled!");
                        }
                    }
                    "status" => {
                        let (chain_length, difficulty) = miner.get_blockchain_info();
                        println!("📈 STATUS:");
                        println!("   Miner ID: {}", miner.miner_id);
                        println!("   Balance: {} coins", miner.get_balance());
                        println!("   Blockchain length: {} blocks", chain_length);
                        println!("   Current difficulty: {}", difficulty);
                    }
                    "balance" => {
                        println!("💰 Current balance: {} coins", miner.get_balance());
                    }
                    "blocks" => {
                        let (chain_length, difficulty) = miner.get_blockchain_info();
                        println!("⛓️  BLOCKCHAIN INFO:");
                        println!("   Total blocks: {}", chain_length);
                        println!("   Current difficulty: {}", difficulty);
                        
                        let blockchain = miner.blockchain.lock().unwrap();
                        if let Some(last_block) = blockchain.get_last_block() {
                            println!("   Last block ID: {}", last_block.id);
                            println!("   Last block hash: {}...", &last_block.hash[..16]);
                        }
                    }
                    "help" => {
                        println!("🆘 AVAILABLE COMMANDS:");
                        println!("   1/logs  - Toggle mining logs");
                        println!("   status  - Show mining status");
                        println!("   balance - Show your balance");
                        println!("   blocks  - Show blockchain info");
                        println!("   help    - Show this help");
                        println!("   q/quit  - Stop mining and exit");
                    }
                    _ => {
                        println!("❓ Unknown command. Type 'help' for available commands.");
                    }
                }
            }
            Err(error) => {
                println!("❌ Error reading input: {}", error);
                break;
            }
        }
    }
    
    // Wait for mining thread to finish
    if let Ok(result) = mining_thread.join() {
        println!("⛏️  Mining result: {}", result);
    }
    
    // Save final blockchain state
    {
        let blockchain = miner.blockchain.lock().unwrap();
        blockchain.save_to_file();
    }
    
    println!("👋 Goodbye! Thanks for mining VanillaCoin!");
    println!("💾 Your blockchain has been saved to blockchain.json");
}