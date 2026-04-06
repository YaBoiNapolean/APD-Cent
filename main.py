import discord
from discord.ext import commands
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
GSP_ORANGE = discord.Color.from_rgb(255, 165, 0)
GSP_RED = discord.Color.from_rgb(255, 0, 0)
GSP_NAVY = discord.Color.from_rgb(0, 0, 50)

# Channel IDs (Replace with your actual IDs)
CHANNELS = {
    'arrest_logs': 1486825085439443125,
    'citation_logs': 1486885813013844148,
    'infractions': 1486847816507719753,
    'medal_requests': 1486846567548715189
}

# Role IDs (Replace with your actual IDs)
ROLES = {
    'strike_1': 1486876700242608268,
    'strike_2': 1486876780190105630,
    'up_for_ban': 1486876910905593866,
    'strike_confirmer': 1486883804550398053
}

# --- DATABASE & UTILITIES ---

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (id INTEGER PRIMARY KEY AUTOINCREMENT, suspect TEXT, officer_id INTEGER, charges TEXT, mugshot TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (id INTEGER PRIMARY KEY AUTOINCREMENT, suspect TEXT, officer_id INTEGER, reason TEXT, timestamp TEXT)''')
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

def get_footer(itx: discord.Interaction, action="Logged"):
    return f"{action} by {itx.user.display_name} | {datetime.utcnow().strftime('%m/%d/%Y')}"

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

@bot.tree.command(name='arrest_log', description='Log a GSP arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, mugshot_url: str = None):
    ts = datetime.utcnow().strftime('%B %d, %Y at %H:%M')
    embed = discord.Embed(title="Arrest", color=GSP_ORANGE)
    embed.set_author(name="Georgia State Patrol")
    embed.add_field(name="Suspect", value=suspect, inline=False)
    embed.add_field(name="Charges", value=charges, inline=False)
    embed.add_field(name="Date", value=ts, inline=False)
    if mugshot_url: embed.set_image(url=mugshot_url)
    embed.set_footer(text=get_footer(itx))
    
    chan = bot.get_channel(CHANNELS['arrest_logs'])
    if chan: await chan.send(embed=embed)
    await itx.response.send_message("Arrest logged.", ephemeral=True)

@bot.tree.command(name='bolo_issue', description='Issue a BOLO with expiration')
@app_commands.choices(expires=[
    app_commands.Choice(name="24 Hours", value=24),
    app_commands.Choice(name="48 Hours", value=48),
    app_commands.Choice(name="72 Hours", value=72),
    app_commands.Choice(name="1 Week", value=168)
])
async def bolo_issue(itx: discord.Interaction, suspect: str, reason: str, vehicle: str, plate: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    expiry_dt = datetime.utcnow() + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="BOLO", color=GSP_RED)
    embed.set_author(name="Georgia State Patrol")
    embed.add_field(name="Suspect", value=suspect, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Vehicle", value=vehicle, inline=True)
    embed.add_field(name="Plate", value=plate, inline=True)
    embed.add_field(name="Expires", value=expiry_dt.strftime('%B %d, %Y at %H:%M'), inline=False)
    embed.set_footer(text=f"ID: {id_code} | {get_footer(itx)}")

    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO bolos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, vehicle, plate, res.id, itx.channel_id, expiry_dt.isoformat(), datetime.utcnow().isoformat()))
        await db.commit()

@bot.tree.command(name='warrant_submit', description='Submit an Arrest Warrant with expiration')
@app_commands.choices(expires=[
    app_commands.Choice(name="24 Hours", value=24),
    app_commands.Choice(name="48 Hours", value=48),
    app_commands.Choice(name="72 Hours", value=72),
    app_commands.Choice(name="1 Week", value=168)
])
async def warrant_submit(itx: discord.Interaction, suspect: str, risk_level: str, reason: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    expiry_dt = datetime.utcnow() + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="Arrest Warrant", color=GSP_RED)
    embed.set_author(name="Georgia State Patrol")
    embed.add_field(name="Suspect", value=suspect, inline=False)
    embed.add_field(name="Risk Level", value=risk_level, inline=True)
    embed.add_field(name="Expires", value=expiry_dt.strftime('%B %d, %Y at %H:%M'), inline=True)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.set_footer(text=f"ID: {id_code} | {get_footer(itx)}")

    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO warrants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, risk_level, res.id, itx.channel_id, expiry_dt.isoformat(), datetime.utcnow().isoformat()))
        await db.commit()

@bot.tree.command(name='search_user', description='Search all active records for a specific suspect')
async def search_user(itx: discord.Interaction, name: str):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        c_w = await db.execute("SELECT id_code FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (name, now))
        warrants = await c_w.fetchall()
        c_b = await db.execute("SELECT id_code FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (name, now))
        bolos = await c_b.fetchall()

    embed = discord.Embed(title=f"Search Results for {name}", color=discord.Color.dark_grey())
    embed.add_field(name="Warrants", value=f"{'✅ 0 active' if not warrants else '⚠️ ' + ', '.join([w[0] for w in warrants])}", inline=False)
    embed.add_field(name="BOLOs", value=f"{'✅ 0 active' if not bolos else '🚨 ' + ', '.join([b[0] for b in bolos])}", inline=False)
    embed.set_footer(text=f"GSP Database Query")
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='search_active', description='View all globally active GSP records')
async def search_active(itx: discord.Interaction):
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        c_w = await db.execute("SELECT suspect, id_code FROM warrants WHERE expiry_timestamp > ?", (now,))
        w_records = await c_w.fetchall()
        c_b = await db.execute("SELECT suspect, id_code FROM bolos WHERE expiry_timestamp > ?", (now,))
        b_records = await c_b.fetchall()

    unique_suspects = set([r[0] for r in w_records] + [r[0] for r in b_records])
    embed = discord.Embed(title="Active Warrants/BOLOs In-Game", color=GSP_RED)
    embed.description = f"Found **{len(unique_suspects)}** player(s) with active records."

    for i, suspect in enumerate(unique_suspects, 1):
        recs = [f"Warrant `{w[1]}`" for w in w_records if w[0] == suspect] + [f"BOLO `{b[1]}`" for b in b_records if b[0] == suspect]
        embed.add_field(name=f"{i}. {suspect}", value=f"⚠️ {' • '.join(recs)}", inline=False)

    await itx.response.send_message(embed=embed)

@bot.tree.command(name='clear_record', description='Wipe a record from existence via ID')
async def clear_record(itx: discord.Interaction, id_code: str):
    id_code = id_code.upper()
    async with aiosqlite.connect(DATABASE) as db:
        for table in ['warrants', 'bolos']:
            cursor = await db.execute(f"SELECT message_id, channel_id FROM {table} WHERE id_code = ?", (id_code,))
            row = await cursor.fetchone()
            if row:
                await db.execute(f"DELETE FROM {table} WHERE id_code = ?", (id_code,))
                await db.commit()
                try:
                    msg = await bot.get_channel(row[1]).fetch_message(row[0])
                    await msg.delete()
                except: pass
                await itx.response.send_message(f"✅ Record {id_code} cleared.", ephemeral=True)
                return
    await itx.response.send_message("ID not found.", ephemeral=True)

@bot.tree.command(name='infraction_log', description='Log an officer infraction')
async def infraction_log(itx: discord.Interaction, officer: discord.Member, reason: str, proof: str = "N/A"):
    embed = discord.Embed(title="Internal Affairs - Infraction", color=GSP_RED)
    embed.set_author(name="Georgia State Patrol")
    embed.add_field(name="Officer", value=officer.mention, inline=False)
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Proof", value=proof, inline=False)
    await itx.response.send_message(embed=embed, view=StrikeConfirmView(officer.id, reason))

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f'GSP Bot Online: {bot.user}')

bot.run(os.environ.get('DISCORD_TOKEN'))