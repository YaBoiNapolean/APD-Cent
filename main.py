import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import aiosqlite
import random
import string
import os
from datetime import datetime, timedelta

# --- CONFIGURATION ---
# Set to your mounted volume path for persistence
DATABASE = '/data/gsp_bot.db' 
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Visual Identity
GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f")
GSP_RED = discord.Color.red()
GSP_YELLOW = 0xFFFF00
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# IDs - ENSURE THESE MATCH YOUR SERVER
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

# --- DATABASE & UTILITIES ---

def get_pst_time():
    return (datetime.utcnow() - timedelta(hours=8)).strftime('%B %d, %Y at %H:%M')

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, secondaries TEXT, charges TEXT, mugshot TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, vehicle TEXT, location TEXT, reason TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, vehicle TEXT, plate TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, risk_level TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, issuer_id INTEGER, reason TEXT, punishment TEXT, proof TEXT, msg_url TEXT, is_active INTEGER DEFAULT 1, is_processed INTEGER DEFAULT 0, expiry_timestamp TEXT, timestamp TEXT)''')
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

# --- UI COMPONENTS ---

class ExpirySelect(ui.Select):
    def __init__(self, callback_func):
        options = [
            discord.SelectOption(label="24 Hours", value="24"),
            discord.SelectOption(label="48 Hours", value="48"),
            discord.SelectOption(label="72 Hours", value="72"),
            discord.SelectOption(label="1 Week", value="168"),
        ]
        super().__init__(placeholder="Select Expiration Time...", options=options)
        self.callback_func = callback_func

    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class AutoStrikeView(ui.View):
    def __init__(self, trooper: discord.Member, infraction_ids: list):
        super().__init__(timeout=None)
        self.trooper = trooper
        self.infraction_ids = infraction_ids

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.success)
    async def confirm(self, itx: discord.Interaction, button: ui.Button):
        if itx.guild.get_role(ROLES['strike_confirmer']) not in itx.user.roles:
            return await itx.response.send_message("❌ Unauthorized.", ephemeral=True)

        s1, s2, ub = itx.guild.get_role(ROLES['strike_1']), itx.guild.get_role(ROLES['strike_2']), itx.guild.get_role(ROLES['up_for_ban'])
        
        target_role = s1
        if ub in self.trooper.roles:
             return await itx.response.send_message("⚠️ Already Up for Ban.", ephemeral=True)
        if s2 in self.trooper.roles: target_role = ub
        elif s1 in self.trooper.roles: target_role = s2

        await self.trooper.add_roles(target_role)
        async with aiosqlite.connect(DATABASE) as db:
            for i_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (i_id,))
            await db.commit()

        await itx.response.send_message("✅ **Strike Confirmed**", ephemeral=True)
        await itx.message.delete()

    @ui.button(label='Deny Strike', style=discord.ButtonStyle.danger)
    async def deny(self, itx: discord.Interaction, button: ui.Button):
        if itx.guild.get_role(ROLES['strike_confirmer']) not in itx.user.roles:
            return await itx.response.send_message("❌ Unauthorized.", ephemeral=True)
        async with aiosqlite.connect(DATABASE) as db:
            for i_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (i_id,))
            await db.commit()
        await itx.message.delete()

class ResetConfirmView(ui.View):
    @ui.button(label="CONFIRM WIPE", style=discord.ButtonStyle.danger)
    async def confirm_wipe(self, itx: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DATABASE) as db:
            for table in ["arrests", "citations", "bolos", "warrants", "infractions"]:
                await db.execute(f"DELETE FROM {table}")
            await db.commit()
        await itx.response.send_message("⚠️ **Database has been wiped.**", ephemeral=True)

# --- COMMANDS ---

@bot.tree.command(name='infraction_log', description='Log an infraction and check for 3-strike trigger')
async def infraction_log(itx: discord.Interaction, trooper: discord.Member, reason: str, punishment: str, proof: str = "None"):
    if not await is_cmd_channel(itx): return
    async def process_infraction(itx_select: discord.Interaction, hours: int):
        ts = get_pst_time(); expiry = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        
        log_embed = discord.Embed(title="⚠️ **INFRACTION LOGGED**", color=GSP_RED)
        log_embed.description = f"{SEPARATOR}\n\n**Trooper:** {trooper.mention}\n**Reason:** {reason}\n**Expires:** {hours}h\n\n{SEPARATOR}"
        log_msg = await bot.get_channel(CHANNELS['infractions']).send(embed=log_embed)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO infractions (user_id, issuer_id, reason, punishment, proof, msg_url, expiry_timestamp, timestamp) VALUES (?,?,?,?,?,?,?,?)",
                             (trooper.id, itx.user.id, reason, punishment, proof, log_msg.jump_url, expiry, ts))
            await db.commit()
            async with db.execute("SELECT id FROM infractions WHERE user_id = ? AND is_active = 1 AND is_processed = 0", (trooper.id,)) as c:
                active = await c.fetchall()
            if len(active) >= 3:
                ids = [r[0] for r in active]
                embed = discord.Embed(title="⚖️ **STRIKE CONFIRMATION**", color=GSP_RED)
                embed.description = f"{SEPARATOR}\n\n**Officer:** {trooper.mention}\n**Trigger:** 3 Active Infractions detected.\n\n{SEPARATOR}"
                await bot.get_channel(CHANNELS['strike_confirm']).send(embed=embed, view=AutoStrikeView(trooper, ids))
        await itx_select.response.send_message("✅ Infraction recorded.", ephemeral=True)
    await itx.response.send_message("Select duration:", view=ui.View().add_item(ExpirySelect(process_infraction)), ephemeral=True)

@bot.tree.command(name='search_active', description='View all active BOLOs and Warrants globally')
async def search_active(itx: discord.Interaction):
    if not await is_cmd_channel(itx): return
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT suspect, id_code FROM bolos WHERE expiry_timestamp > ?", (now,)) as c: bolos = await c.fetchall()
        async with db.execute("SELECT suspect, id_code FROM warrants WHERE expiry_timestamp > ?", (now,)) as c: warrants = await c.fetchall()
    embed = discord.Embed(title="📂 **ACTIVE NCIC RECORDS**", color=GSP_CUSTOM_ORANGE)
    b_list = "\n".join([f"• {b[0]} ({b[1]})" for b in bolos]) if bolos else "None"
    w_list = "\n".join([f"• {w[0]} ({w[1]})" for w in warrants]) if warrants else "None"
    embed.description = f"{SEPARATOR}\n\n🚨 **Active BOLOs:**\n{b_list}\n\n⚖️ **Active Warrants:**\n{w_list}\n\n{SEPARATOR}"
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='arrest_log', description='Log a suspect arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, secondaries: str = "None", mugshot_url: str = "None"):
    if not await is_cmd_channel(itx): return
    id_code = await generate_unique_id(); ts = get_pst_time()
    embed = discord.Embed(title="🚨 **ARREST LOGGED**", color=GSP_CUSTOM_ORANGE)
    mugshot_link = f"[Click for Full Screen]({mugshot_url})" if mugshot_url != "None" else "None"
    embed.description = (f"{SEPARATOR}\n\n**ID:** {id_code}\n**Officer:** {itx.user.mention}\n"
                         f"**Secondaries:** {secondaries}\n\n**Suspect:** {suspect}\n**Charges:** {charges}\n"
                         f"**Mugshot:** {mugshot_link}\n\n**Date:** {ts}\n{SEPARATOR}")
    if mugshot_url != "None": embed.set_image(url=mugshot_url)
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, secondaries, charges, mugshot_url, ts))
        await db.commit()
    await bot.get_channel(CHANNELS['arrest_logs']).send(embed=embed)
    await itx.response.send_message("✅ Arrest logged.", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a citation')
async def citation_log(itx: discord.Interaction, suspect: str, vehicle: str, location: str, reason: str):
    if not await is_cmd_channel(itx): return
    id_code = await generate_unique_id(); ts = get_pst_time()
    embed = discord.Embed(title="🎫 **CITATION ISSUED**", color=GSP_YELLOW)
    embed.description = f"{SEPARATOR}\n\n**ID:** {id_code}\n**Officer:** {itx.user.mention}\n**Suspect:** {suspect}\n**Vehicle:** {vehicle}\n**Reason:** {reason}\n\n**Date:** {ts}\n{SEPARATOR}"
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, vehicle, location, reason, ts))
        await db.commit()
    await bot.get_channel(CHANNELS['citation_logs']).send(embed=embed)
    await itx.response.send_message("✅ Citation logged.", ephemeral=True)

@bot.tree.command(name='bolo_log', description='Issue a BOLO')
async def bolo_log(itx: discord.Interaction, suspect: str, vehicle: str, reason: str, plate: str = "Unknown"):
    if not await is_cmd_channel(itx): return
    async def callback(itx_s, h):
        id_c = await generate_unique_id(); ts = get_pst_time(); ex = (datetime.utcnow() + timedelta(hours=h)).isoformat()
        embed = discord.Embed(title=f"🚨 **BOLO: {suspect}**", color=GSP_RED)
        embed.description = f"{SEPARATOR}\n\n**ID:** {id_c}\n**Vehicle:** {vehicle}\n**Reason:** {reason}\n**Expires:** {h}h\n\n{SEPARATOR}"
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO bolos VALUES (?,?,?,?,?,?,?,?)", (id_c, suspect, itx.user.id, reason, vehicle, plate, ex, ts))
            await db.commit()
        await itx_s.response.send_message(embed=embed)
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpirySelect(callback)), ephemeral=True)

@bot.tree.command(name='warrant_log', description='Issue a Warrant')
async def warrant_log(itx: discord.Interaction, suspect: str, reason: str, risk_level: str = "Standard"):
    if not await is_cmd_channel(itx): return
    async def callback(itx_s, h):
        id_c = await generate_unique_id(); ts = get_pst_time(); ex = (datetime.utcnow() + timedelta(hours=h)).isoformat()
        embed = discord.Embed(title=f"⚖️ **WARRANT: {suspect}**", color=GSP_RED)
        embed.description = f"{SEPARATOR}\n\n**ID:** {id_c}\n**Risk:** {risk_level}\n**Reason:** {reason}\n**Expires:** {h}h\n\n{SEPARATOR}"
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO warrants VALUES (?,?,?,?,?,?,?)", (id_c, suspect, itx.user.id, reason, risk_level, ex, ts))
            await db.commit()
        await itx_s.response.send_message(embed=embed)
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpirySelect(callback)), ephemeral=True)

@bot.tree.command(name='trooper_performance', description='Trooper statistics')
async def trooper_performance(itx: discord.Interaction, trooper: discord.Member):
    if not await is_cmd_channel(itx): return
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT COUNT(*) FROM arrests WHERE officer_id = ?", (trooper.id,)) as c: p_arr = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM citations WHERE officer_id = ?", (trooper.id,)) as c: cit = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM infractions WHERE user_id = ? AND is_active = 1", (trooper.id,)) as c: inf = (await c.fetchone())[0]
    strike = "None"
    if itx.guild.get_role(ROLES['up_for_ban']) in trooper.roles: strike = "Up for Ban"
    elif itx.guild.get_role(ROLES['strike_2']) in trooper.roles: strike = "Strike 2"
    elif itx.guild.get_role(ROLES['strike_1']) in trooper.roles: strike = "Strike 1"
    embed = discord.Embed(title=f"📊 **PERFORMANCE: {trooper.display_name}**", color=GSP_CUSTOM_ORANGE)
    embed.description = f"{SEPARATOR}\n\n**Arrests:** {p_arr}\n**Citations:** {cit}\n**Active Infractions:** {inf}\n**Current Strike:** {strike}\n\n{SEPARATOR}"
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='reset_all_data', description='Wipe the entire database (ADMIN ONLY)')
@app_commands.checks.has_permissions(administrator=True)
async def reset_all_data(itx: discord.Interaction):
    embed = discord.Embed(title="🛑 **CRITICAL: DATABASE WIPE**", color=discord.Color.red())
    embed.description = "Are you sure? This deletes everything permanently."
    await itx.response.send_message(embed=embed, view=ResetConfirmView(), ephemeral=True)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f'GSP Bot Online | Persistence: {DATABASE}')

bot.run(os.getenv("DISCORD_TOKEN"))