import discord  # Fixed: lowercase 'i'
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiosqlite
import random
import string
import os  # Added to handle Railway file paths
from datetime import datetime, timedelta

# --- RAILWAY DATABASE FIX ---
# This ensures the database file is created in the correct server directory
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, 'gsp_bot.db')

# --- CONFIGURATION ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f")
GSP_RED = discord.Color.red()
GSP_YELLOW = 0xFFFF00
GSP_SOFT_RED = discord.Color.from_str("#ff7f7f")

# 🚨 IMPORTANT: Update these IDs when you move to the new server!
CMD_CHANNEL_ID = 1486886286081130526

CHANNELS = {
   'arrest_logs': 1486825085439443125,
   'citation_logs': 1486885813013844148,
   'infractions': 1486847816507719753,
   'strike_confirm': 1486824029980463206
}

ROLES = {
   'strike_1': 1486876700242608268,
   'strike_2': 1486876780190105630,
   'up_for_ban': 1486876910905593866,
   'strike_confirmer': 1486883804550398053
}

# --- UTILITIES ---

def get_pst_time():
   return (datetime.utcnow() - timedelta(hours=8)).strftime('%B %d, %Y at %H:%M')

async def init_db():
   async with aiosqlite.connect(DATABASE) as db:
       await db.execute('''CREATE TABLE IF NOT EXISTS arrests (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, secondaries TEXT, charges TEXT, mugshot TEXT, timestamp TEXT)''')
       await db.execute('''CREATE TABLE IF NOT EXISTS citations (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, vehicle TEXT, location TEXT, reason TEXT, timestamp TEXT)''')
       await db.execute('''CREATE TABLE IF NOT EXISTS bolos (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, vehicle TEXT, plate TEXT, message_id INTEGER, channel_id INTEGER, expiry_timestamp TEXT, timestamp TEXT)''')
       await db.execute('''CREATE TABLE IF NOT EXISTS warrants (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, risk_level TEXT, message_id INTEGER, channel_id INTEGER, expiry_timestamp TEXT, timestamp TEXT)''')
       await db.execute('''CREATE TABLE IF NOT EXISTS infractions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, issuer_id INTEGER, reason TEXT, punishment TEXT, proof TEXT, msg_url TEXT, is_active INTEGER DEFAULT 1, timestamp TEXT)''')
       await db.commit()

async def generate_unique_id():
   async with aiosqlite.connect(DATABASE) as db:
       while True:
           num = ''.join(random.choices(string.digits, k=4))
           new_id = f"GSP{num}"
           async with db.execute("SELECT 1 FROM bolos WHERE id_code = ? UNION SELECT 1 FROM warrants WHERE id_code = ?", (new_id, new_id)) as cursor:
               if not await cursor.fetchone():
                   return new_id

async def is_cmd_channel(itx: discord.Interaction):
   if itx.channel.id != CMD_CHANNEL_ID:
       await itx.response.send_message(f"❌ This command can only be used in <#{CMD_CHANNEL_ID}>.", ephemeral=True)
       return False
   return True

# --- VIEWS ---

class ResetConfirmView(ui.View):
   def __init__(self):
       super().__init__(timeout=30)

   @ui.button(label='CONFIRM DATA WIPE', style=discord.ButtonStyle.danger)
   async def confirm_reset(self, itx: discord.Interaction, button: ui.Button):
       async with aiosqlite.connect(DATABASE) as db:
           for table in ['arrests', 'citations', 'bolos', 'warrants', 'infractions']:
               await db.execute(f"DELETE FROM {table}")
           await db.commit()
       await itx.response.edit_message(content="⚠️ **DATABASE WIPED.** All logs cleared.", view=None)

   @ui.button(label='Cancel', style=discord.ButtonStyle.secondary)
   async def cancel_reset(self, itx: discord.Interaction, button: ui.Button):
       await itx.response.edit_message(content="❌ Data wipe cancelled.", view=None)

# --- COMMANDS ---

@bot.tree.command(name='arrest_log', description='Log a suspect arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, secondaries: str = "None", mugshot_url: str = "None"):
   if not await is_cmd_channel(itx): return
   id_code = await generate_unique_id(); ts = get_pst_time()
  
   embed = discord.Embed(title="Arrest", color=GSP_CUSTOM_ORANGE)
   mugshot_link = f"[Click for Full Screen]({mugshot_url})" if mugshot_url != "None" else "None"
   embed.description = (f"**___________________________________**\n\n**ID:** {id_code}\n**Primary Officer:** {itx.user.mention}\n"
                        f"**Secondary Officer(s):** {secondaries}\n\n**Suspect:** {suspect}\n**Charges:** {charges}\n"
                        f"**Mugshot:** {mugshot_link}\n\n**Date:** {ts}")
   if mugshot_url != "None": embed.set_image(url=mugshot_url)
   embed.set_footer(text=f"Logged by {itx.user.display_name}")
  
   async with aiosqlite.connect(DATABASE) as db:
       await db.execute("INSERT INTO arrests VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, secondaries, charges, mugshot_url, ts))
       await db.commit()
      
   await bot.get_channel(CHANNELS['arrest_logs']).send(embed=embed)
   await itx.response.send_message("✅ Arrest has been logged in the proper channel.", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a citation')
async def citation_log(itx: discord.Interaction, suspect: str, vehicle: str, location: str, reason: str):
   if not await is_cmd_channel(itx): return
   id_code = await generate_unique_id(); ts = get_pst_time()
  
   embed = discord.Embed(title="Citation Issued", color=GSP_YELLOW)
   embed.description = (f"**___________________________________**\n\n**ID:** {id_code}\n**Officer:** {itx.user.mention}\n"
                        f"**Suspect:** {suspect}\n**Vehicle:** {vehicle}\n**Location:** {location}\n**Reason:** {reason}\n\n**Date:** {ts}")
  
   async with aiosqlite.connect(DATABASE) as db:
       await db.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, vehicle, location, reason, ts))
       await db.commit()
      
   await bot.get_channel(CHANNELS['citation_logs']).send(embed=embed)
   await itx.response.send_message("✅ Citation has been logged in the proper channel.", ephemeral=True)

@bot.tree.command(name='bolo_log', description='Issue a BOLO')
async def bolo_log(itx: discord.Interaction, suspect: str, vehicle: str, reason: str, plate: str = "Unknown"):
   if not await is_cmd_channel(itx): return
   id_code = await generate_unique_id(); ts = get_pst_time()
  
   embed = discord.Embed(title=f"BOLO: {suspect}", color=GSP_RED)
   embed.description = (f"**___________________________________**\n\n**ID:** {id_code}\n**Reason:** {reason}\n"
                        f"**Vehicle:** {vehicle}\n**Plate:** {plate}\n\n**Expires:** 24 Hours\n**Issued by:** {itx.user.mention}")
  
   async with aiosqlite.connect(DATABASE) as db:
       expiry = (datetime.utcnow() + timedelta(hours=24)).isoformat()
       await db.execute("INSERT INTO bolos VALUES (?,?,?,?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, vehicle, plate, 0, 0, expiry, ts))
       await db.commit()
      
   await itx.response.send_message(embed=embed)

@bot.tree.command(name='warrant_log', description='Issue a Warrant')
async def warrant_log(itx: discord.Interaction, suspect: str, reason: str, risk_level: str = "Standard"):
   if not await is_cmd_channel(itx): return
   id_code = await generate_unique_id(); ts = get_pst_time()
  
   embed = discord.Embed(title=f"WARRANT: {suspect}", color=GSP_RED)
   embed.description = (f"**___________________________________**\n\n**ID:** {id_code}\n**Risk Level:** {risk_level}\n"
                        f"**Reason:** {reason}\n\n**Expires:** 7 Days\n**Issued by:** {itx.user.mention}")
  
   async with aiosqlite.connect(DATABASE) as db:
       expiry = (datetime.utcnow() + timedelta(days=7)).isoformat()
       await db.execute("INSERT INTO warrants VALUES (?,?,?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, risk_level, 0, 0, expiry, ts))
       await db.commit()
      
   await itx.response.send_message(embed=embed)

@bot.tree.command(name='search_user', description='NCIC lookup for a specific citizen')
async def search_user(itx: discord.Interaction, name: str):
   if not await is_cmd_channel(itx): return
   now = datetime.utcnow().isoformat()
   async with aiosqlite.connect(DATABASE) as db:
       async with db.execute("SELECT COUNT(*) FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (name, now)) as c: w_c = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (name, now)) as c: b_c = (await c.fetchone())[0]
       async with db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (name,)) as c: last = await c.fetchone()
  
   embed = discord.Embed(title=f"NCIC Results: {name}", color=GSP_CUSTOM_ORANGE)
   embed.description = (f"**___________________________________**\n\n**Active Warrants:** {'✅ 0' if w_c == 0 else f'🚨 {w_c}'}\n"
                        f"**Active BOLOs:** {'✅ 0' if b_c == 0 else f'⚠️ {b_c}'}\n**Last Arrest:** {last[0] if last else 'No Record Found'}")
   await itx.response.send_message(embed=embed)

@bot.tree.command(name='trooper_performance', description='View Trooper statistics')
async def trooper_performance(itx: discord.Interaction, trooper: discord.Member):
   if not await is_cmd_channel(itx): return
   async with aiosqlite.connect(DATABASE) as db:
       async with db.execute("SELECT COUNT(*) FROM arrests WHERE officer_id = ?", (trooper.id,)) as c: p_arr = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM arrests WHERE secondaries LIKE ?", (f"%{trooper.id}%",)) as c: s_arr = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM citations WHERE officer_id = ?", (trooper.id,)) as c: cit = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM warrants WHERE officer_id = ?", (trooper.id,)) as c: war = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM bolos WHERE officer_id = ?", (trooper.id,)) as c: bol = (await c.fetchone())[0]
       async with db.execute("SELECT COUNT(*) FROM infractions WHERE user_id = ? AND is_active = 1", (trooper.id,)) as c: inf = (await c.fetchone())[0]
  
   strike = "None"
   if itx.guild.get_role(ROLES['up_for_ban']) in trooper.roles: strike = "Up for Ban"
   elif itx.guild.get_role(ROLES['strike_2']) in trooper.roles: strike = "Strike 2"
   elif itx.guild.get_role(ROLES['strike_1']) in trooper.roles: strike = "Strike 1"

   embed = discord.Embed(title=f"Trooper Performance - {trooper.display_name}", color=GSP_CUSTOM_ORANGE)
   embed.description = (f"**___________________________________**\n\n**Trooper:** {trooper.mention}\n\n"
                        f"**Primary Officer Arrests:** {p_arr}\n**Secondary Officer Arrests:** {s_arr}\n"
                        f"**Total Citations:** {cit}\n\n**Total Warrants Submitted:** {war}\n"
                        f"**Total BOLOs Submitted:** {bol}\n\n**Active Infractions:** {inf}\n"
                        f"**Current Strikes:** {strike}\n\n**Status:** {'Good Standing' if inf == 0 else '⚠️ Monitoring'}")
   await itx.response.send_message(embed=embed)

@bot.tree.command(name='reset_all_data', description='Wipe all database records')
@app_commands.checks.has_permissions(administrator=True)
async def reset_all_data(itx: discord.Interaction):
   embed = discord.Embed(title="🚨 DATABASE WIPE REQUESTED", color=GSP_SOFT_RED)
   embed.description = "This will permanently delete every log in the database. Continue?"
   await itx.response.send_message(embed=embed, view=ResetConfirmView(), ephemeral=True)

@bot.event
async def on_ready():
   await init_db()
   await bot.tree.sync()
   print(f'GSP Bot Online: {bot.user}')

# Fixed: Railway uses DISCORD_TOKEN environment variable
bot.run(os.getenv("DISCORD_TOKEN"))