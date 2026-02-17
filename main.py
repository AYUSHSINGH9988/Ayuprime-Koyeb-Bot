import os
import telebot
import requests
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pymongo import MongoClient

# --- CONFIGURATION (Aapki Details) ---
BOT_TOKEN = "8489527645:AAGokjooAXkg2L6qXhr0ThG1rPahxjEUQ5Q"
MONGO_URL = "mongodb+srv://gauravsingh576466_db_user:mOuhQVApEQVMpeYr@cluster0.d94qqiv.mongodb.net/BotDatabase?retryWrites=true&w=majority&appName=Cluster0"
ADMIN_ID = 6337657627 

# --- MONGODB CONNECTION ---
try:
    client = MongoClient(MONGO_URL)
    db = client['BotDatabase']
    collection = db['koyeb_services']
    print("✅ MongoDB Connected Successfully!")
except Exception as e:
    print(f"❌ MongoDB Connection Error: {e}")

bot = telebot.TeleBot(BOT_TOKEN)

# --- HEALTH CHECK SERVER (Bot ko sone se rokne ke liye) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()

# --- ADMIN CHECK HELPER ---
def is_admin(message):
    if message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "❌ **Access Denied!** Ye bot private hai.")
        return False
    return True

# --- COMMANDS ---

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    if not is_admin(message): return
    help_text = (
        "🤖 **Koyeb Manager Bot (Repo Version)**\n\n"
        "🛠 **Commands:**\n"
        "1️⃣ `/add name api_key service_id` - Service save karein\n"
        "2️⃣ `/redeploy name` - Service ko restart karein\n"
        "3️⃣ `/logs name` - Latest Deployment Logs mangwayein\n"
        "4️⃣ `/list` - Saved services ki list\n"
        "5️⃣ `/del name` - Service delete karein"
    )
    bot.reply_to(message, help_text, parse_mode="Markdown")

# 1. ADD SERVICE
@bot.message_handler(commands=['add'])
def add_service(message):
    if not is_admin(message): return
    try:
        parts = message.text.split()
        if len(parts) != 4:
            return bot.reply_to(message, "⚠️ Format: `/add name api_key service_id`")
        
        name, key, sid = parts[1], parts[2], parts[3]
        collection.update_one(
            {"name": name},
            {"$set": {"key": key, "sid": sid}},
            upsert=True
        )
        bot.reply_to(message, f"✅ Service `{name}` database mein save ho gayi!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {e}")

# 2. REDEPLOY (Trigger Build)
@bot.message_handler(commands=['redeploy'])
def redeploy_service(message):
    if not is_admin(message): return
    try:
        name = message.text.split()[1]
        data = collection.find_one({"name": name})
        if not data:
            return bot.reply_to(message, f"❌ `{name}` nahi mila.")
        
        url = f"https://app.koyeb.com/v1/services/{data['sid']}/redeploy"
        headers = {
            "Authorization": f"Bearer {data['key']}",
            "Content-Type": "application/json"
        }
        
        bot.reply_to(message, f"🔄 `{name}` redeploy request bhej raha hoon...")
        # use_cache: False ka matlab fresh build (clean cache)
        resp = requests.post(url, headers=headers, json={"use_cache": False})
        
        if resp.status_code in [200, 201]:
            bot.send_message(message.chat.id, f"✅ **Success!** `{name}` ka naya build start ho gaya hai.", parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, f"❌ Failed! Status: {resp.status_code}\nResponse: {resp.text}")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# 3. GET LOGS (Fixed Logic: Service -> Deployment -> Logs)
@bot.message_handler(commands=['logs'])
def get_logs(message):
    if not is_admin(message): return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            return bot.reply_to(message, "⚠️ Use: `/logs name`")

        name = parts[1]
        data = collection.find_one({"name": name})
        if not data:
            return bot.reply_to(message, f"❌ `{name}` database mein nahi mila.")

        bot.send_chat_action(message.chat.id, 'typing')
        bot.reply_to(message, f"🔍 `{name}` ki latest deployment dhoond raha hoon...")

        headers = {"Authorization": f"Bearer {data['key']}"}

        # Step 1: Service details se Latest Deployment ID nikalo
        service_url = f"https://app.koyeb.com/v1/services/{data['sid']}"
        service_resp = requests.get(service_url, headers=headers)
        
        if service_resp.status_code != 200:
            return bot.reply_to(message, f"❌ Service Error: {service_resp.status_code}")

        deployment_id = service_resp.json().get("service", {}).get("latest_deployment_id", "")
        
        if not deployment_id:
            return bot.reply_to(message, "❌ Koi Deployment ID nahi mili. Shayad service nayi hai.")

        # Step 2: Logs download karo
        logs_url = f"https://app.koyeb.com/v1/deployments/{deployment_id}/logs/build" # Build logs pehle try karo
        logs_resp = requests.get(logs_url, headers=headers)
        
        # Agar build logs khali ho, to runtime logs try karo
        log_content = logs_resp.text
        if not log_content.strip():
             logs_url = f"https://app.koyeb.com/v1/deployments/{deployment_id}/logs/runtime"
             log_content = requests.get(logs_url, headers=headers).text

        if not log_content.strip():
            return bot.reply_to(message, "📭 Logs bilkul khali hain (Wait for provisioning).")

        # Step 3: File banakar bhejo
        file_name = f"{name}_logs.txt"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(log_content)
        
        with open(file_name, "rb") as f:
            bot.send_document(message.chat.id, f, caption=f"📄 Logs: `{name}`\n🆔 DID: `{deployment_id}`", parse_mode="Markdown")
        
        os.remove(file_name) # File delete kardo

    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# 4. LIST & DELETE
@bot.message_handler(commands=['list'])
def list_services(message):
    if not is_admin(message): return
    data = list(collection.find())
    if not data: return bot.reply_to(message, "📭 Database Khali hai.")
    
    res = "📋 **Saved Services:**\n"
    for item in data: res += f"- `{item['name']}`\n"
    bot.reply_to(message, res, parse_mode="Markdown")

@bot.message_handler(commands=['del'])
def delete_service(message):
    if not is_admin(message): return
    try:
        name = message.text.split()[1]
        collection.delete_one({"name": name})
        bot.reply_to(message, f"🗑️ Service `{name}` deleted.")
    except:
        bot.reply_to(message, "⚠️ Use: `/del name`")

print("🤖 Bot Started...")
bot.polling(none_stop=True)
