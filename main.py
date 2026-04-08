import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import aiosqlite
import random
import string
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DATABASE = '/data/gsp_bot.db' 
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Visual Identity
GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f")
GSP_RED = discord.Color.red()
GSP_YELLOW = 0xFFFF00
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Channel and Role IDs
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
    'strike_confirmer': 1486883804550398053,
    'supervisor': 1486824300857262140
}

# --- DATABASE & UTILITIES ---

def get_pst_time():
    """Returns current PST time as a formatted string."""
    return (datetime.utcnow() - timedelta(hours=8)).strftime('%B %d, %Y at %H:%M')

def format_time_ago(ts_string):
    """Calculates how long ago a timestamp occurred."""
    try:
        past = datetime.strptime(ts_string, '%B %d, %Y at %H:%M')
        now = datetime.utcnow() - timedelta(hours=8)
        diff = now - past
        if diff.days > 0:
            return f"{diff.days} days ago"
        if diff.seconds // 3600 > 0:
            return f"{diff.seconds // 3600} hours ago"
        return f"{diff.seconds // 60} minutes ago"
    except Exception:
        return "Unknown"

async def init_db():
    """Initializes all required database tables."""
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            secondaries TEXT, charges TEXT, mugshot TEXT, timestamp TEXT)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            vehicle TEXT, location TEXT, reason TEXT, timestamp TEXT)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            reason TEXT, vehicle TEXT, plate TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            reason TEXT, risk_level TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, issuer_id INTEGER, 
            reason TEXT, punishment TEXT, proof TEXT, msg_url TEXT, is_active INTEGER DEFAULT 1, 
            is_processed INTEGER DEFAULT 0, expiry_timestamp TEXT, timestamp TEXT)''')
        await db.commit()

async def generate_unique_id():
    """Generates a unique GSP-XXXX ID for records."""
    async with aiosqlite.connect(DATABASE) as db:
        while True:
            num = ''.join(random.choices(string.digits, k=4))
            new_id = f"GSP{num}"
            async with db.execute("SELECT 1 FROM bolos WHERE id_code = ? UNION SELECT 1 FROM warrants WHERE id_code = ?", (new_id, new_id)) as cursor:
                if not await cursor.fetchone():
                    return new_id

async def is_cmd_channel(itx: discord.Interaction):
    """Checks if the command is being run in the correct channel."""
    if itx.channel.id != CMD_CHANNEL_ID:
        await itx.response.send_message(f"❌ This command can only be used in <#{CMD_CHANNEL_ID}>.", ephemeral=True)
        return False
    return True

# --- UI COMPONENTS ---

class StrikeConfirmView(ui.View):
    def __init__(self, trooper: discord.Member, infraction_ids: list):
        super().__init__(timeout=None)
        self.trooper = trooper
        self.infraction_ids = infraction_ids

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.success)
    async def confirm_strike(self, itx: discord.Interaction, button: ui.Button):
        confirmer_role = itx.guild.get_role(ROLES['strike_confirmer'])
        if confirmer_role not in itx.user.roles:
            return await itx.response.send_message("❌ Only Strike Confirmers can use this.", ephemeral=True)
            
        s1 = itx.guild.get_role(ROLES['strike_1'])
        s2 = itx.guild.get_role(ROLES['strike_2'])
        ub = itx.guild.get_role(ROLES['up_for_ban'])
        
        target_role = s1
        if ub in self.trooper.roles:
            return await itx.response.send_message("⚠️ Trooper is already 'Up For Ban'.", ephemeral=True)
        elif s2 in self.trooper.roles:
            target_role = ub
        elif s1 in self.trooper.roles:
            target_role = s2
        
        await self.trooper.add_roles(target_role)
        
        async with aiosqlite.connect(DATABASE) as db:
            for inf_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (inf_id,))
            await db.commit()

        log_embed = discord.Embed(title="Strike Issued", color=GSP_RED)
        log_embed.description = f"{SEPARATOR}\n**Officer:** {self.trooper.mention}\n**Level:** {target_role.name}\n{SEPARATOR}"
        
        inf_channel = bot.get_channel(CHANNELS['infractions'])
        if inf_channel:
            await inf_channel.send(content=f"{self.trooper.mention}", embed=log_embed)

        confirm_embed = log_embed.copy()
        confirm_embed.title = "✅ STRIKE CONFIRMED"
        await itx.response.edit_message(content=f"✅ Done.", embed=confirm_embed, view=None)

class ExpiryDropdown(ui.Select):
    def __init__(self, callback_func):
        options = [
            discord.SelectOption(label="24 Hours", value="24"),
            discord.SelectOption(label="48 Hours", value="48"),
            discord.SelectOption(label="72 Hours", value="72"),
            discord.SelectOption(label="1 Week", value="168"),
        ]
        super().__init__(placeholder="How long should this last?", options=options)
        self.callback_func = callback_func

    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class ClearRecordConfirm(ui.View):
    def __init__(self, original_user, officer_id, record_id, table):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.officer_id = int(officer_id)
        self.record_id = record_id
        self.table = table

    @ui.button(label="Permanently Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, itx: discord.Interaction, button: ui.Button):
        if itx.user.id != self.original_user.id:
            return await itx.response.send_message("❌ This is not your menu.", ephemeral=True)
        
        is_supervisor = itx.guild.get_role(ROLES['supervisor']) in itx.user.roles
        if itx.user.id != self.officer_id and not is_supervisor:
            return await itx.response.send_message("❌ You didn't create this record and aren't a supervisor.", ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(f"DELETE FROM {self.table} WHERE id_code = ?", (self.record_id,))
            await db.commit()
            
        await itx.response.send_message(f"🗑️ Record `{self.record_id}` has been deleted.", ephemeral=True)
        await itx.message.delete()

# --- COMMANDS ---

@bot.tree.command(name='info', description='Get contact information for bug reports')
async def info(itx: discord.Interaction):
    if not await is_cmd_channel(itx): return
    embed = discord.Embed(
        description="If you have any questions or find any bugs, please DM YaBoi_Napolean.",
        color=GSP_CUSTOM_ORANGE
    )
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='trooper_performance', description='View full lifetime history and strike status')
async def trooper_performance(itx: discord.Interaction, trooper: discord.Member):
    if not await is_cmd_channel(itx): return
    
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT COUNT(*) FROM arrests WHERE officer_id = ?", (trooper.id,)) as c:
            arr_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM citations WHERE officer_id = ?", (trooper.id,)) as c:
            cit_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bolos WHERE officer_id = ?", (trooper.id,)) as c:
            blo_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM warrants WHERE officer_id = ?", (trooper.id,)) as c:
            war_count = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM infractions WHERE user_id = ?", (trooper.id,)) as c:
            inf_count = (await c.fetchone())[0]

    s1, s2, ub = itx.guild.get_role(ROLES['strike_1']), itx.guild.get_role(ROLES['strike_2']), itx.guild.get_role(ROLES['up_for_ban'])
    
    current_strike = "None"
    if ub in trooper.roles: current_strike = "⚠️ Up For Ban"
    elif s2 in trooper.roles: current_strike = "Strike 2"
    elif s1 in trooper.roles: current_strike = "Strike 1"

    perf_embed = discord.Embed(title=f"📈 PERFORMANCE REPORT: {trooper.display_name}", color=GSP_CUSTOM_ORANGE)
    perf_embed.set_thumbnail(url=trooper.display_avatar.url)
    perf_embed.description = (
        f"{SEPARATOR}\n\n"
        f"⚡ **Current Strike:** `{current_strike}`\n\n"
        f"🚨 **Total Arrests:** `{arr_count}`\n"
        f"🎫 **Total Citations:** `{cit_count}`\n"
        f"📡 **Total BOLOs:** `{blo_count}`\n"
        f"⚖️ **Total Warrants:** `{war_count}`\n"
        f"⚠️ **Total Infractions:** `{inf_count}`\n\n"
        f"{SEPARATOR}"
    )
    perf_embed.set_footer(text=f"Historical Data Sync • {get_pst_time()}")
    await itx.response.send_message(embed=perf_embed)

@bot.tree.command(name='search_record', description='Lookup full details of any GSP ID')
async def search_record(itx: discord.Interaction, record_id: str):
    if not await is_cmd_channel(itx): return
    
    rid = record_id.upper()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT * FROM arrests WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="🚨 ARREST RECORD", color=GSP_CUSTOM_ORANGE)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Charges:** {row[4]}\n**Date:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)
        
        async with db.execute("SELECT * FROM citations WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="🎫 CITATION RECORD", color=GSP_YELLOW)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[5]}\n**Date:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

        async with db.execute("SELECT * FROM bolos WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="📡 BOLO RECORD", color=GSP_RED)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

        async with db.execute("SELECT * FROM warrants WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="⚖️ WARRANT RECORD", color=GSP_RED)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[5]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

    await itx.response.send_message(f"❌ Record `{rid}` not found.", ephemeral=True)

@bot.tree.command(name='clear_record', description='Delete a record by ID (Officer/Supervisor only)')
async def clear_record(itx: discord.Interaction, record_id: str):
    if not await is_cmd_channel(itx): return
    rid = record_id.upper()
    async with aiosqlite.connect(DATABASE) as db:
        for table in ["arrests", "citations", "bolos", "warrants"]:
            async with db.execute(f"SELECT officer_id FROM {table} WHERE id_code = ?", (rid,)) as c:
                row = await c.fetchone()
                if row:
                    view = ClearRecordConfirm(itx.user, row[0], rid, table)
                    return await itx.response.send_message(f"🚨 Delete `{rid}` from `{table}`?", view=view, ephemeral=True)
    await itx.response.send_message("❌ ID not found.", ephemeral=True)

@bot.tree.command(name='infraction_log', description='Log misconduct (Supervisor Only)')
async def infraction_log(itx: discord.Interaction, trooper: discord.Member, reason: str, punishment: str, proof: str = "None"):
    if not await is_cmd_channel(itx): return
    if itx.guild.get_role(ROLES['supervisor']) not in itx.user.roles:
        return await itx.response.send_message("❌ Access Denied.", ephemeral=True)

    async def complete_infraction(itx_select, hours):
        ts, expire_at = get_pst_time(), (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        embed = discord.Embed(title="⚠️ INFRACTION LOGGED", color=GSP_RED)
        embed.description = f"**Trooper:** {trooper.mention}\n**Reason:** {reason}\n**Punishment:** {punishment}"
        log_msg = await bot.get_channel(CHANNELS['infractions']).send(content=f"{trooper.mention}", embed=embed)
        
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('''INSERT INTO infractions (user_id, issuer_id, reason, punishment, proof, msg_url, expiry_timestamp, timestamp) 
                             VALUES (?,?,?,?,?,?,?,?)''', (trooper.id, itx.user.id, reason, punishment, proof, log_msg.jump_url, expire_at, ts))
            await db.commit()
            async with db.execute("SELECT id FROM infractions WHERE user_id = ? AND is_processed = 0", (trooper.id,)) as c:
                active_rows = await c.fetchall()
            if len(active_rows) >= 3:
                ids = [r[0] for r in active_rows]
                strike_box = discord.Embed(title="⚖️ STRIKE ELIGIBILITY", description=f"{trooper.mention} has 3 active infractions.", color=GSP_RED)
                await bot.get_channel(CHANNELS['strike_confirm']).send(content=f"{trooper.mention}", embed=strike_box, view=StrikeConfirmView(trooper, ids))
        await itx_select.response.send_message("✅ Logged.", ephemeral=True)

    await itx.response.send_message("Set duration:", view=ui.View().add_item(ExpiryDropdown(complete_infraction)), ephemeral=True)

@bot.tree.command(name='search_user', description='NCIC lookup for suspect criminal history')
async def search_user(itx: discord.Interaction, suspect_name: str):
    if not await is_cmd_channel(itx): return
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT id_code, reason FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c:
            warrants = await c.fetchall()
        async with db.execute("SELECT id_code, reason FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c:
            bolos = await c.fetchall()
        async with db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (suspect_name,)) as c:
            last_arrest = await c.fetchone()

    status_color = GSP_RED if (warrants or bolos) else discord.Color.green()
    embed = discord.Embed(title=f"🔍 NCIC FILE: {suspect_name}", color=status_color)
    w_text = "\n".join([f"• `{w[0]}`: {w[1]}" for w in warrants]) if warrants else "No Active Warrants"
    b_text = "\n".join([f"• `{b[0]}`: {b[1]}" for b in bolos]) if bolos else "No Active BOLOs"
    embed.description = f"{SEPARATOR}\n**Warrants:**\n{w_text}\n\n**BOLOs:**\n{b_text}\n\n**Last Arrest:**\n{format_time_ago(last_arrest[0]) if last_arrest else 'Never'}\n{SEPARATOR}"
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='arrest_log', description='Log an arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, secondaries: str = "None", mugshot_url: str = "None"):
    if not await is_cmd_channel(itx): return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, secondaries, charges, mugshot_url, ts))
        await db.commit()
    embed = discord.Embed(title="🚨 ARREST LOGGED", color=GSP_CUSTOM_ORANGE)
    embed.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Charges:** {charges}"
    if mugshot_url != "None": embed.set_image(url=mugshot_url)
    await bot.get_channel(CHANNELS['arrest_logs']).send(embed=embed)
    await itx.response.send_message(f"✅ Logged `{id_code}`", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a citation')
async def citation_log(itx: discord.Interaction, suspect: str, vehicle: str, location: str, reason: str):
    if not await is_cmd_channel(itx): return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, vehicle, location, reason, ts))
        await db.commit()
    embed = discord.Embed(title="🎫 CITATION ISSUED", description=f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Reason:** {reason}", color=GSP_YELLOW)
    await bot.get_channel(CHANNELS['citation_logs']).send(embed=embed)
    await itx.response.send_message("✅ Logged.", ephemeral=True)

@bot.tree.command(name='bolo_log', description='Issue a BOLO')
async def bolo_log(itx: discord.Interaction, suspect: str, vehicle: str, reason: str, plate: str = "Unknown"):
    if not await is_cmd_channel(itx): return
    async def post_bolo(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO bolos VALUES (?,?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, vehicle, plate, expire, ts))
            await db.commit()
        await itx_s.response.send_message(embed=discord.Embed(title="📡 BOLO ACTIVE", description=f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Vehicle:** {vehicle}", color=GSP_RED))
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpiryDropdown(post_bolo)), ephemeral=True)

@bot.tree.command(name='warrant_log', description='Issue a warrant')
async def warrant_log(itx: discord.Interaction, suspect: str, reason: str, risk: str = "Medium"):
    if not await is_cmd_channel(itx): return
    async def post_warrant(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO warrants VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, risk, expire, ts))
            await db.commit()
        await itx_s.response.send_message(embed=discord.Embed(title="⚖️ WARRANT ACTIVE", description=f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Reason:** {reason}", color=GSP_RED))
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpiryDropdown(post_warrant)), ephemeral=True)

@bot.tree.command(name="user_info", description="Lookup trooper account data")
async def user_info(itx: discord.Interaction, trooper: discord.Member):
    if not await is_cmd_channel(itx): return
    e = discord.Embed(title=f"👤 PROFILE: {trooper.display_name}", color=GSP_CUSTOM_ORANGE)
    e.description = f"{SEPARATOR}\n**ID:** `{trooper.id}`\n**Role:** {trooper.top_role.mention}\n**Joined:** {trooper.joined_at.strftime('%Y-%m-%d')}\n{SEPARATOR}"
    await itx.response.send_message(embed=e)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f"GSP Systems Online.")

bot.run(os.getenv("DISCORD_TOKEN"))