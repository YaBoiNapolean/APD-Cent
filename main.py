import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiosqlite
import random
import string
import os
from datetime import datetime, timedelta

# --- CONFIGURATION ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

DATABASE = 'gsp_bot.db'

# Colors
GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f") # Updated Hex
GSP_YELLOW = 0xFFFF00 
GSP_RED = discord.Color.red()
GSP_NAVY = discord.Color.from_rgb(0, 0, 50)

# IDs - Ensure these match your server
CHANNELS = {
    'arrest_logs': 1486825085439443125,
    'citation_logs': 1486885813013844148,
    'infractions': 1486847816507719753
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
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, charges TEXT, mugshot TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, vehicle TEXT, location TEXT, reason TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, vehicle TEXT, plate TEXT, message_id INTEGER, channel_id INTEGER, expiry_timestamp TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, reason TEXT, risk_level TEXT, message_id INTEGER, channel_id INTEGER, expiry_timestamp TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, officer_id INTEGER, reason TEXT, proof TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS strikes (user_id INTEGER PRIMARY KEY, strike_count INTEGER DEFAULT 0)''')
        await db.commit()

async def generate_unique_id():
    async with aiosqlite.connect(DATABASE) as db:
        while True:
            num = ''.join(random.choices(string.digits, k=4))
            new_id = f"GSP{num}"
            async with db.execute("SELECT 1 FROM bolos WHERE id_code = ? UNION SELECT 1 FROM warrants WHERE id_code = ?", (new_id, new_id)) as cursor:
                if not await cursor.fetchone():
                    return new_id

# --- BACKGROUND TASKS ---

@tasks.loop(hours=1)
async def cleanup_expired_records():
    now = (datetime.utcnow() - timedelta(hours=8)).isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("DELETE FROM bolos WHERE expiry_timestamp < ?", (now,))
        await db.execute("DELETE FROM warrants WHERE expiry_timestamp < ?", (now,))
        await db.commit()

# --- STRIKE SYSTEM ---

class StrikeConfirmView(ui.View):
    def __init__(self, user_id: int, reason: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.reason = reason

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.danger)
    async def confirm_strike(self, itx: discord.Interaction, button: ui.Button):
        confirmer_role = itx.guild.get_role(ROLES['strike_confirmer'])
        if confirmer_role not in itx.user.roles:
            await itx.response.send_message("Unauthorized.", ephemeral=True)
            return

        async with aiosqlite.connect(DATABASE) as db:
            cursor = await db.execute('SELECT strike_count FROM strikes WHERE user_id = ?', (self.user_id,))
            row = await cursor.fetchone()
            count = (row[0] + 1) if row else 1
            await db.execute('INSERT OR REPLACE INTO strikes (user_id, strike_count) VALUES (?, ?)', (self.user_id, count))
            await db.commit()

        member = itx.guild.get_member(self.user_id)
        if member:
            s1, s2, ban = [itx.guild.get_role(ROLES[r]) for r in ['strike_1', 'strike_2', 'up_for_ban']]
            if count == 1: await member.add_roles(s1)
            elif count == 2: 
                await member.remove_roles(s1)
                await member.add_roles(s2)
            elif count >= 3:
                await member.remove_roles(s2)
                await member.add_roles(ban)

        await itx.response.edit_message(content=f"✅ Strike {count} confirmed for <@{self.user_id}>.", view=None, embed=None)

# --- SLASH COMMANDS ---

@bot.tree.command(name='arrest_log', description='Log a formal GSP arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, mugshot_url: str = None):
    id_code = await generate_unique_id()
    ts = get_pst_time()
    
    embed = discord.Embed(title="Arrest", color=GSP_CUSTOM_ORANGE)
    embed.description = (
        f"**ID:** {id_code}\n"
        f"**Primary Officer:** {itx.user.mention} ({itx.user.display_name})\n\n"
        f"**Suspect:** {suspect}\n"
        f"**Charges:** {charges}\n\n"
        f"**Date:** {ts}"
    )
    if mugshot_url: embed.set_image(url=mugshot_url)
    
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests VALUES (?, ?, ?, ?, ?, ?)", (id_code, suspect, itx.user.id, charges, mugshot_url, ts))
        await db.commit()

    chan = bot.get_channel(CHANNELS['arrest_logs'])
    if chan: await chan.send(embed=embed)
    await itx.response.send_message("✅ Arrest logged.", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a formal GSP citation')
async def citation_log(itx: discord.Interaction, person: str, vehicle: str, location: str, reason: str):
    cid = await generate_unique_id()
    ts = get_pst_time()
    
    embed = discord.Embed(title="Citation", color=GSP_YELLOW)
    embed.description = (
        f"**ID:** {cid}\n"
        f"**Person:** {person}\n"
        f"**Vehicle:** {vehicle}\n"
        f"**Location:** {location}\n"
        f"**Reason:** {reason}\n\n"
        f"**Officer:** {itx.user.display_name}\n"
        f"**Date:** {ts}\n\n"
        f"Logged by {itx.user.display_name}"
    )

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations VALUES (?, ?, ?, ?, ?, ?, ?)", (cid, person, itx.user.id, vehicle, location, reason, ts))
        await db.commit()

    chan = bot.get_channel(CHANNELS['citation_logs'])
    if chan: await chan.send(embed=embed)
    await itx.response.send_message(f"✅ Citation {cid} filed.", ephemeral=True)

@bot.tree.command(name='bolo_issue', description='Issue a BOLO')
@app_commands.choices(expires=[app_commands.Choice(name="24 Hours", value=24), app_commands.Choice(name="1 Week", value=168)])
async def bolo_issue(itx: discord.Interaction, suspect: str, reason: str, vehicle: str, plate: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    ts = get_pst_time()
    expiry_dt = (datetime.utcnow() - timedelta(hours=8)) + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="BOLO", color=GSP_RED)
    embed.description = (
        f"**Suspect:** {suspect}\n"
        f"**Reason:** {reason}\n"
        f"**Vehicle Model:** {vehicle}\n"
        f"**Plate:** {plate}\n\n"
        f"**Issued:** {ts}\n"
        f"**Expires:** {expiry_dt.strftime('%B %d, %Y at %H:%M')}\n\n"
        f"Use /bolo clear {id_code} to clear this BOLO | Logged by {itx.user.display_name}"
    )
    
    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO bolos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, vehicle, plate, res.id, itx.channel_id, expiry_dt.isoformat(), ts))
        await db.commit()

@bot.tree.command(name='warrant_submit', description='Submit an Arrest Warrant')
@app_commands.choices(expires=[app_commands.Choice(name="24 Hours", value=24), app_commands.Choice(name="2 Weeks", value=336)])
async def warrant_submit(itx: discord.Interaction, suspect: str, risk_level: str, reason: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    ts = get_pst_time()
    expiry_dt = (datetime.utcnow() - timedelta(hours=8)) + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="Arrest Warrant", color=GSP_RED)
    embed.description = (
        f"**Suspect:** {suspect}\n"
        f"**Type:** Arrest\n"
        f"**Risk Level:** {risk_level}\n"
        f"**Reason:** {reason}\n\n"
        f"**Issued By:** {itx.user.display_name}\n"
        f"**Date:** {ts}\n"
        f"**Expires:** {expires.name} from issue date\n\n"
        f"Use /warrant clear {id_code} to clear this warrant | Logged by {itx.user.display_name}"
    )

    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO warrants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, risk_level, res.id, itx.channel_id, expiry_dt.isoformat(), ts))
        await db.commit()

@bot.tree.command(name='search_active', description='View all currently active records')
async def search_active(itx: discord.Interaction):
    now_pst = (datetime.utcnow() - timedelta(hours=8)).isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT suspect, id_code, 'Warrant' FROM warrants WHERE expiry_timestamp > ?", (now_pst,)) as c: warrants = await c.fetchall()
        async with db.execute("SELECT suspect, id_code, 'BOLO' FROM bolos WHERE expiry_timestamp > ?", (now_pst,)) as c: bolos = await c.fetchall()

    all_records = warrants + bolos
    if not all_records:
        await itx.response.send_message(embed=discord.Embed(description="✅ No active Warrants or BOLO’s", color=GSP_RED))
        return

    grouped = {}
    for suspect, id_code, rtype in all_records:
        if suspect not in grouped: grouped[suspect] = []
        grouped[suspect].append(f"{rtype} {id_code}")

    desc = [f"Found **{len(grouped)}** player(s) with active records currently online\n"]
    for i, (suspect, records) in enumerate(grouped.items(), 1):
        desc.append(f"**{i}. {suspect}**")
        desc.append(f"⚠️ {' • ⚠️ '.join(records)}")
        desc.append("**Team:** Civilian | **Vehicles:** Unknown")
        desc.append("**Location:** Unknown\n")

    await itx.response.send_message(embed=discord.Embed(title="Active Warrants/BOLOs In-Game", description="\n".join(desc), color=GSP_RED))

@bot.tree.command(name='search_user', description='Search NCIC Database')
async def search_user(itx: discord.Interaction, name: str):
    now_pst = datetime.utcnow() - timedelta(hours=8)
    async with aiosqlite.connect(DATABASE) as db:
        c_w = await db.execute("SELECT COUNT(*) FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (name, now_pst.isoformat()))
        w_count = (await c_w.fetchone())[0]
        c_b = await db.execute("SELECT COUNT(*) FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (name, now_pst.isoformat()))
        b_count = (await c_b.fetchone())[0]
        c_c = await db.execute("SELECT COUNT(*) FROM citations WHERE suspect = ?", (name,))
        cit_count = (await c_c.fetchone())[0]
        c_a = await db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (name,))
        last_arrest = await c_a.fetchone()

    embed = discord.Embed(title=f"Search Results for {name}", color=GSP_CUSTOM_ORANGE)
    embed.description = (
        f"**Warrants**\n{'✅ 0 active' if w_count == 0 else f'⚠️ {w_count} active'}\n"
        f"**BOLOs**\n{'✅ 0 active' if b_count == 0 else f'⚠️ {b_count} active'}\n"
        f"**Recent Citations**\n{'✅ 0 citation(s)' if cit_count == 0 else f'⚠️ {cit_count} citation(s)'}\n"
        f"**Recent Arrest**\n{f'Arrested {last_arrest[0]}' if last_arrest else 'No records found'}"
    )
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='infraction_log', description='Log officer infraction')
async def infraction_log(itx: discord.Interaction, officer: discord.Member, reason: str, proof: str = "N/A"):
    embed = discord.Embed(title="Internal Affairs - Infraction", color=GSP_CUSTOM_ORANGE)
    embed.description = f"**Officer:** {officer.mention}\n**Reason:** {reason}\n**Proof:** {proof}"
    chan = bot.get_channel(CHANNELS['infractions'])
    if chan: await chan.send(embed=embed, view=StrikeConfirmView(officer.id, reason))
    await itx.response.send_message("✅ Infraction sent.", ephemeral=True)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    if not cleanup_expired_records.is_running(): cleanup_expired_records.start()
    print(f'GSP Bot Online: {bot.user}')

bot.run(os.environ.get('DISCORD_TOKEN'))