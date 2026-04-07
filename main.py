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
GSP_ORANGE = discord.Color.from_rgb(255, 165, 0)
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
    """Returns formatted string for PST time (UTC-8)."""
    return (datetime.utcnow() - timedelta(hours=8)).strftime('%B %d, %Y at %H:%M')

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

# --- BACKGROUND TASKS ---

@tasks.loop(hours=1)
async def cleanup_expired_records():
    """Removes expired BOLOs and Warrants from the DB automatically."""
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
            await itx.response.send_message("Unauthorized. You do not have the Strike Confirmer role.", ephemeral=True)
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
@app_commands.describe(suspect="Name of the suspect", charges="List of charges", mugshot_url="Link to image")
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, mugshot_url: str = None):
    ts = get_pst_time()
    embed = discord.Embed(title="Arrest Report", description="**___________________________________**", color=GSP_ORANGE)
    embed.add_field(name="Suspect", value=f"**{suspect}**", inline=False)
    embed.add_field(name="Charges", value=f"**{charges}**", inline=False)
    embed.add_field(name="Date", value=f"**{ts}**", inline=False)
    if mugshot_url: embed.set_image(url=mugshot_url)
    embed.set_footer(text=f"Logged by {itx.user.display_name}")
    
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests (suspect, officer_id, charges, mugshot, timestamp) VALUES (?, ?, ?, ?, ?)",
                         (suspect, itx.user.id, charges, mugshot_url, ts))
        await db.commit()

    chan = bot.get_channel(CHANNELS['arrest_logs'])
    if chan: await chan.send(embed=embed)
    await itx.response.send_message("✅ Arrest logged.", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a formal GSP citation')
@app_commands.describe(person="Name of suspect", vehicle="Vehicle desc", location="Street/Area", reason="Violation", officer="Your Name/Callsign")
async def citation_log(itx: discord.Interaction, person: str, vehicle: str, location: str, reason: str, officer: str):
    cid = await generate_unique_id()
    ts = get_pst_time()
    embed = discord.Embed(title="Citation", description="**___________________________________**", color=GSP_YELLOW)
    embed.add_field(name="ID", value=f"**{cid}**", inline=False)
    embed.add_field(name="Person", value=f"**{person}**", inline=False)
    embed.add_field(name="Vehicle", value=f"**{vehicle}**", inline=False)
    embed.add_field(name="Location", value=f"**{location}**", inline=False)
    embed.add_field(name="Reason", value=f"**{reason}**", inline=False)
    embed.add_field(name="Officer", value=f"**{officer}**", inline=False)
    embed.add_field(name="Date", value=f"**{ts}**", inline=False)
    embed.set_footer(text=f"Logged by {itx.user.display_name}")

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations (suspect, officer_id, reason, timestamp) VALUES (?, ?, ?, ?)",
                         (person, itx.user.id, f"[{location}] {reason}", ts))
        await db.commit()

    chan = bot.get_channel(CHANNELS['citation_logs'])
    if chan: await chan.send(embed=embed)
    await itx.response.send_message(f"✅ Citation {cid} filed.", ephemeral=True)

@bot.tree.command(name='bolo_issue', description='Issue a BOLO with expiration')
@app_commands.describe(suspect="Suspect Name", reason="Reason", vehicle="Vehicle", plate="Plate")
@app_commands.choices(expires=[
    app_commands.Choice(name="24 Hours", value=24),
    app_commands.Choice(name="48 Hours", value=48),
    app_commands.Choice(name="1 Week", value=168)
])
async def bolo_issue(itx: discord.Interaction, suspect: str, reason: str, vehicle: str, plate: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    ts = get_pst_time()
    expiry_dt = (datetime.utcnow() - timedelta(hours=8)) + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="BOLO Issued", color=GSP_RED)
    embed.add_field(name="ID", value=f"**{id_code}**", inline=False)
    embed.add_field(name="Suspect", value=f"**{suspect}**", inline=False)
    embed.add_field(name="Reason", value=f"**{reason}**", inline=False)
    embed.add_field(name="Vehicle", value=f"**{vehicle}**", inline=False)
    embed.add_field(name="Plate", value=f"**{plate}**", inline=False)
    embed.add_field(name="Expires", value=f"**{expiry_dt.strftime('%B %d, %Y at %H:%M')}**", inline=False)
    
    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO bolos VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, vehicle, plate, res.id, itx.channel_id, expiry_dt.isoformat(), ts))
        await db.commit()

@bot.tree.command(name='warrant_submit', description='Submit an Arrest Warrant')
@app_commands.describe(suspect="Name", risk_level="Low/Med/High", reason="Crime")
@app_commands.choices(expires=[
    app_commands.Choice(name="24 Hours", value=24),
    app_commands.Choice(name="1 Week", value=168)
])
async def warrant_submit(itx: discord.Interaction, suspect: str, risk_level: str, reason: str, expires: app_commands.Choice[int]):
    id_code = await generate_unique_id()
    ts = get_pst_time()
    expiry_dt = (datetime.utcnow() - timedelta(hours=8)) + timedelta(hours=expires.value)
    
    embed = discord.Embed(title="Arrest Warrant", description="**___________________________________**", color=GSP_RED)
    embed.add_field(name="ID", value=f"**{id_code}**", inline=False)
    embed.add_field(name="Suspect", value=f"**{suspect}**", inline=False)
    embed.add_field(name="Risk Level", value=f"**{risk_level}**", inline=False)
    embed.add_field(name="Reason", value=f"**{reason}**", inline=False)
    embed.add_field(name="Expires", value=f"**{expiry_dt.strftime('%B %d, %Y at %H:%M')}**", inline=False)

    await itx.response.send_message(embed=embed)
    res = await itx.original_response()

    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO warrants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_code, suspect, itx.user.id, reason, risk_level, res.id, itx.channel_id, expiry_dt.isoformat(), ts))
        await db.commit()

@bot.tree.command(name='search_active', description='View all currently active Warrants and BOLOs')
async def search_active(itx: discord.Interaction):
    now_pst = (datetime.utcnow() - timedelta(hours=8)).isoformat()
    
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT suspect, id_code, 'Warrant' FROM warrants WHERE expiry_timestamp > ?", (now_pst,)) as cursor:
            warrants = await cursor.fetchall()
        async with db.execute("SELECT suspect, id_code, 'BOLO' FROM bolos WHERE expiry_timestamp > ?", (now_pst,)) as cursor:
            bolos = await cursor.fetchall()

    all_records = warrants + bolos
    
    if not all_records:
        embed = discord.Embed(description="✅ No active Warrants or BOLO’s", color=GSP_RED)
        await itx.response.send_message(embed=embed)
        return

    grouped = {}
    for suspect, id_code, record_type in all_records:
        if suspect not in grouped:
            grouped[suspect] = []
        grouped[suspect].append(f"{record_type} {id_code}")

    description_lines = [f"Found **{len(grouped)}** player(s) with active records\n"]
    
    for i, (suspect, records) in enumerate(grouped.items(), 1):
        description_lines.append(f"**{i}. {suspect}**")
        record_str = " • ".join([f"⚠️ {r}" for r in records]) # Plain text IDs
        description_lines.append(record_str)
        description_lines.append("Team: Civilian | Vehicles: Unknown")
        description_lines.append("Location: Unknown\n")

    embed = discord.Embed(title="Active Warrants/BOLOs In-Game", description="\n".join(description_lines), color=GSP_RED)
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='search_user', description='Search NCIC Database')
@app_commands.describe(name="Player name to look up")
async def search_user(itx: discord.Interaction, name: str):
    now_pst = datetime.utcnow() - timedelta(hours=8)
    async with aiosqlite.connect(DATABASE) as db:
        c_w = await db.execute("SELECT id_code FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (name, now_pst.isoformat()))
        warrants = await c_w.fetchall()
        c_b = await db.execute("SELECT id_code FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (name, now_pst.isoformat()))
        bolos = await c_b.fetchall()
        c_c = await db.execute("SELECT reason, timestamp FROM citations WHERE suspect = ? ORDER BY id DESC", (name,))
        citations = await c_c.fetchall()
        c_a = await db.execute("SELECT COUNT(*) FROM arrests WHERE suspect = ?", (name,))
        arrest_count = (await c_a.fetchone())[0]

    embed = discord.Embed(title=f"NCIC Query: {name}", color=GSP_ORANGE)
    embed.add_field(name="Active Warrants", value=f"**{'✅ Clear' if not warrants else ', '.join([w[0] for w in warrants])}**", inline=False)
    embed.add_field(name="Active BOLOs", value=f"**{'✅ Clear' if not bolos else ', '.join([b[0] for b in bolos])}**", inline=False)
    embed.add_field(name="Total Arrests", value=f"**{arrest_count}**", inline=True)
    
    if citations:
        last_dt = datetime.strptime(citations[0][1], '%B %d, %Y at %H:%M')
        diff = now_pst - last_dt
        time_ago = f"{diff.days}d ago" if diff.days > 0 else f"{diff.seconds // 3600}h ago"
        embed.add_field(name="Last Citation", value=f"**{time_ago}**", inline=True)
        embed.add_field(name="Recent History", value="\n".join([f"• {c[0]}" for c in citations[:3]]), inline=False)
    else:
        embed.add_field(name="Citation History", value="**No prior citations**", inline=False)
    
    await itx.response.send_message(embed=embed)

@bot.tree.command(name='infraction_log', description='Log officer infraction')
@app_commands.describe(officer="Member to strike", reason="Policy violated", proof="Evidence link")
async def infraction_log(itx: discord.Interaction, officer: discord.Member, reason: str, proof: str = "N/A"):
    embed = discord.Embed(title="Internal Affairs - Infraction", color=GSP_ORANGE)
    embed.add_field(name="Officer", value=f"**{officer.mention}**", inline=False)
    embed.add_field(name="Reason", value=f"**{reason}**", inline=False)
    embed.add_field(name="Proof", value=f"**{proof}**", inline=False)
    
    chan = bot.get_channel(CHANNELS['infractions'])
    if chan: await chan.send(embed=embed, view=StrikeConfirmView(officer.id, reason))
    await itx.response.send_message("✅ Infraction sent for supervisor review.", ephemeral=True)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    if not cleanup_expired_records.is_running():
        cleanup_expired_records.start()
    print(f'GSP Bot Online: {bot.user}')

bot.run(os.environ.get('DISCORD_TOKEN'))