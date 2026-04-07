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
GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f")
GSP_YELLOW = 0xFFFF00 
GSP_RED = discord.Color.red()

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
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, charges TEXT, mugshot TEXT, timestamp TEXT)''')
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

# --- VIEWS ---

class StrikeActionView(ui.View):
    def __init__(self, target_id: int, infraction_ids: list):
        super().__init__(timeout=None)
        self.target_id = target_id
        self.infraction_ids = infraction_ids

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.danger)
    async def confirm(self, itx: discord.Interaction, button: ui.Button):
        conf_role = itx.guild.get_role(ROLES['strike_confirmer'])
        if conf_role not in itx.user.roles:
            return await itx.response.send_message("Unauthorized.", ephemeral=True)

        member = itx.guild.get_member(self.target_id)
        if not member:
            return await itx.response.send_message("User no longer in server.", ephemeral=True)

        s1, s2, ban = [itx.guild.get_role(ROLES[r]) for r in ['strike_1', 'strike_2', 'up_for_ban']]
        
        if s2 in member.roles:
            await member.add_roles(ban)
            status = "Up for Ban"
        elif s1 in member.roles:
            await member.add_roles(s2)
            await member.remove_roles(s1)
            status = "Strike 2"
        else:
            await member.add_roles(s1)
            status = "Strike 1"

        async with aiosqlite.connect(DATABASE) as db:
            placeholders = ','.join(['?'] * len(self.infraction_ids))
            await db.execute(f"UPDATE infractions SET is_active = 0 WHERE id IN ({placeholders})", self.infraction_ids)
            await db.commit()

        await itx.response.edit_message(content=f"✅ Strike confirmed. {member.mention} promoted to **{status}**.", embed=None, view=None)

    @ui.button(label='Deny', style=discord.ButtonStyle.secondary)
    async def deny(self, itx: discord.Interaction, button: ui.Button):
        conf_role = itx.guild.get_role(ROLES['strike_confirmer'])
        if conf_role not in itx.user.roles:
            return await itx.response.send_message("Unauthorized.", ephemeral=True)
        await itx.message.delete()

class ResetConfirmView(ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    @ui.button(label='CONFIRM DATA WIPE', style=discord.ButtonStyle.danger)
    async def confirm_reset(self, itx: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DATABASE) as db:
            tables = ['arrests', 'citations', 'bolos', 'warrants', 'infractions']
            for table in tables:
                await db.execute(f"DELETE FROM {table}")
            await db.commit()
        await itx.response.edit_message(content="⚠️ **DATABASE WIPED.** All logs, warrants, and infractions have been deleted.", view=None)

# --- SLASH COMMANDS ---

@bot.tree.command(name='reset_all_data', description='⚠️ DANGER: Deletes ALL data from the database')
@app_commands.checks.has_permissions(administrator=True)
async def reset_all_data(itx: discord.Interaction):
    await itx.response.send_message(
        "🚨 **ARE YOU SURE?** This will permanently delete all arrests, citations, BOLOs, warrants, and infractions. This cannot be undone.",
        view=ResetConfirmView(),
        ephemeral=True
    )

@bot.tree.command(name='infraction_log', description='Log an officer infraction')
async def infraction_log(itx: discord.Interaction, officer: discord.Member, reason: str, punishment: str, proof: str):
    ts = get_pst_time()
    embed = discord.Embed(title="Department of Justice - Infraction", color=GSP_CUSTOM_ORANGE)
    embed.description = (
        f"**Officer:** {officer.mention}\n\n"
        f"**Reason:** {reason}\n"
        f"**Punishment:** {punishment}\n\n"
        f"**Proof:** {proof}\n"
        f"**Appealable:** ✅ | Appealable in 6 days.\n"
        f"**Notes:** N/A\n\n"
        f"**Signed:** {itx.user.mention}\n"
        f"**___________________________________**\n\n"
        f"*If you believe this infraction is false, feel free to contact the infraction issuer personally or open a ticket.*"
    )
    embed.set_footer(text=f"Issued by {itx.user.display_name}")

    chan = bot.get_channel(CHANNELS['infractions'])
    msg = await chan.send(embed=embed)
    
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO infractions (user_id, issuer_id, reason, punishment, proof, msg_url, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (officer.id, itx.user.id, reason, punishment, proof, msg.jump_url, ts))
        await db.commit()
        
        async with db.execute("SELECT id, msg_url FROM infractions WHERE user_id = ? AND is_active = 1", (officer.id,)) as cursor:
            active = await cursor.fetchall()
            
            if len(active) >= 3:
                conf_chan = bot.get_channel(CHANNELS['strike_confirm'])
                links = "\n".join([f"• [Infraction #{row[0]}]({row[1]})" for row in active[:3]])
                ids = [row[0] for row in active[:3]]
                
                conf_embed = discord.Embed(title="Strike Confirmation", color=GSP_RED)
                conf_embed.description = (
                    f"**___________________________________**\n\n"
                    f"**User up for Strike:** {officer.mention}\n\n"
                    f"**Active Infractions:**\n{links}"
                )
                conf_embed.set_footer(text="this is an automatic message")
                await conf_chan.send(embed=conf_embed, view=StrikeActionView(officer.id, ids))

    await itx.response.send_message(f"✅ Infraction logged for {officer.display_name}.", ephemeral=True)

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
        # Check active infractions for user
        c_i = await db.execute("SELECT COUNT(*) FROM infractions WHERE user_id = (SELECT officer_id FROM arrests WHERE suspect = ? LIMIT 1) AND is_active = 1", (name,))
        inf_count = (await c_i.fetchone())[0] if c_i else 0
        c_a = await db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (name,))
        last_arrest = await c_a.fetchone()

    embed = discord.Embed(title=f"Search Results for {name}", color=GSP_CUSTOM_ORANGE)
    embed.description = (
        f"**Warrants**\n{'✅ 0 active' if w_count == 0 else f'⚠️ {w_count} active'}\n"
        f"**BOLOs**\n{'✅ 0 active' if b_count == 0 else f'⚠️ {b_count} active'}\n"
        f"**Active Infractions**\n{'✅ 0 active' if inf_count == 0 else f'⚠️ {inf_count} active'}\n"
        f"**Recent Arrest**\n{f'Arrested {last_arrest[0]}' if last_arrest else 'No records found'}"
    )
    await itx.response.send_message(embed=embed)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print(f'GSP Bot Online: {bot.user}')

bot.run(os.environ.get('DISCORD_TOKEN'))