import os
import discord
from discord.ext import commands
from discord import app_commands, ui
import aiosqlite
import random
import string
from datetime import datetime, timedelta, timezone

# --- CONFIGURATION ---
DATABASE = '/data/gsp_bot.db' 

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

# Visual Identity
GSP_CUSTOM_ORANGE = discord.Color.from_str("#ff640f")
GSP_RED = discord.Color.red()
GSP_YELLOW = 0xFFFF00
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Channel IDs
CMD_CHANNEL_ID = 1486886286081130526
CHANNELS = {
    'arrest_logs': 1486825085439443125,
    'citation_logs': 1486885813013844148,
    'infractions': 1486847816507719753,
    'strike_confirm': 1486824029980463206
}

# Role IDs
ROLES = {
    'strike_1': 1486876700242608268,
    'strike_2': 1486876780190105630,
    'up_for_ban': 1486876910905593866,
    'strike_confirmer': 1486883804550398053,
    'supervisor': 1486824300857262140
}

# --- DATABASE & UTILITIES ---

def get_pst_time():
    utc_now = datetime.now(timezone.utc)
    pst_now = utc_now - timedelta(hours=8)
    return pst_now.strftime('%B %d, %Y at %H:%M')

def format_time_ago(ts_string):
    try:
        past = datetime.strptime(ts_string, '%B %d, %Y at %H:%M')
        now = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=8)
        diff = now - past
        if diff.days > 0: return f"{diff.days} days ago"
        hours = diff.seconds // 3600
        if hours > 0: return f"{hours} hours ago"
        return f"{diff.seconds // 60} minutes ago"
    except:
        return "Unknown"

async def init_db():
    db_dir = os.path.dirname(DATABASE)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
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
            new_id = f"GSP{''.join(random.choices(string.digits, k=4))}"
            query = "SELECT 1 FROM arrests WHERE id_code = ? UNION SELECT 1 FROM citations WHERE id_code = ? UNION SELECT 1 FROM bolos WHERE id_code = ? UNION SELECT 1 FROM warrants WHERE id_code = ?"
            async with db.execute(query, (new_id, new_id, new_id, new_id)) as cursor:
                if not await cursor.fetchone(): return new_id

async def is_cmd_channel(itx: discord.Interaction):
    if itx.channel.id != CMD_CHANNEL_ID:
        await itx.response.send_message(f"❌ Commands restricted to <#{CMD_CHANNEL_ID}>.", ephemeral=True)
        return False
    return True

# --- UI COMPONENTS ---

class StrikeConfirmView(ui.View):
    """Handles the Supervisor decision for a Strike Alert."""
    def __init__(self, trooper: discord.Member, infraction_data: list):
        super().__init__(timeout=None)
        self.trooper = trooper
        self.infraction_ids = [row[0] for row in infraction_data]

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.success)
    async def confirm_strike(self, itx: discord.Interaction, button: ui.Button):
        if itx.guild.get_role(ROLES['strike_confirmer']) not in itx.user.roles:
            return await itx.response.send_message("❌ Unauthorized.", ephemeral=True)
            
        s1, s2, ub = [itx.guild.get_role(ROLES[r]) for r in ['strike_1', 'strike_2', 'up_for_ban']]
        target_role = s1
        display_name = "Strike 1"

        if ub in self.trooper.roles:
            return await itx.response.send_message("⚠️ Already Up For Termination.", ephemeral=True)
        elif s2 in self.trooper.roles:
            target_role, display_name = ub, "Up For Termination"
        elif s1 in self.trooper.roles:
            target_role, display_name = s2, "Strike 2"
        
        await self.trooper.add_roles(target_role)
        
        async with aiosqlite.connect(DATABASE) as db:
            for inf_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (inf_id,))
            await db.commit()

        log_embed = discord.Embed(title="**STRIKE ACTION CONFIRMED**", color=GSP_RED)
        log_embed.description = f"{SEPARATOR}\n**Trooper:** {self.trooper.mention}\n**New Status:** {display_name}\n{SEPARATOR}"
        log_embed.set_footer(text=f"Confirmed by {itx.user.display_name}")
        
        inf_channel = bot.get_channel(CHANNELS['infractions'])
        if inf_channel: await inf_channel.send(content=f"{self.trooper.mention}", embed=log_embed)
        await itx.response.edit_message(content="✅ Strike applied.", embed=log_embed, view=None)

    @ui.button(label='Decline Strike', style=discord.ButtonStyle.danger)
    async def decline_strike(self, itx: discord.Interaction, button: ui.Button):
        if itx.guild.get_role(ROLES['strike_confirmer']) not in itx.user.roles:
            return await itx.response.send_message("❌ Unauthorized.", ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            for inf_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (inf_id,))
            await db.commit()

        await itx.message.delete()
        await itx.response.send_message(f"✅ Strike for {self.trooper.mention} was declined and infractions were cleared from strike-eligibility.", ephemeral=True)

class ExpiryDropdown(ui.Select):
    def __init__(self, callback_func):
        options = [discord.SelectOption(label="24 Hours", value="24"), discord.SelectOption(label="48 Hours", value="48"), discord.SelectOption(label="72 Hours", value="72"), discord.SelectOption(label="1 Week", value="168")]
        super().__init__(placeholder="Duration Selection", options=options)
        self.callback_func = callback_func
    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class InfractionExpiryDropdown(ui.Select):
    def __init__(self, callback_func):
        options = [
            discord.SelectOption(label="24 Hours", value="24"), discord.SelectOption(label="48 Hours", value="48"), discord.SelectOption(label="72 Hours", value="72"),
            discord.SelectOption(label="1 Week", value="168"), discord.SelectOption(label="2 Weeks", value="336"), discord.SelectOption(label="3 Weeks", value="504"),
            discord.SelectOption(label="1 Month", value="720"), discord.SelectOption(label="1.5 Months", value="1080"), discord.SelectOption(label="2 Months", value="1440")
        ]
        super().__init__(placeholder="Select Infraction Expiry", options=options)
        self.callback_func = callback_func
    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class ClearRecordConfirm(ui.View):
    def __init__(self, original_user, officer_id, record_id, table):
        super().__init__(timeout=60)
        self.original_user, self.officer_id, self.record_id, self.table = original_user, int(officer_id), record_id, table
    @ui.button(label="Permanently Delete", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, itx: discord.Interaction, button: ui.Button):
        if itx.user.id != self.original_user.id: return await itx.response.send_message("❌ Not your menu.", ephemeral=True)
        if itx.user.id != self.officer_id and itx.guild.get_role(ROLES['supervisor']) not in itx.user.roles: return await itx.response.send_message("❌ No permission.", ephemeral=True)
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(f"DELETE FROM {self.table} WHERE id_code = ?", (self.record_id,))
            await db.commit()
        await itx.response.send_message(f"🗑️ `{self.record_id}` deleted.", ephemeral=True)
        await itx.message.delete()

# --- COMMANDS ---

@bot.tree.command(name='info', description='Bot support information')
async def info(itx: discord.Interaction):
    if not await is_cmd_channel(itx): return
    e = discord.Embed(description="If you have any questions or find any bugs, please DM **YaBoi_Napolean**.", color=GSP_CUSTOM_ORANGE)
    e.set_footer(text=f"Requested by {itx.user.display_name}")
    await itx.response.send_message(embed=e)

@bot.tree.command(name='trooper_performance', description='View trooper lifetime stats')
async def trooper_performance(itx: discord.Interaction, trooper: discord.Member):
    if not await is_cmd_channel(itx): return
    async with aiosqlite.connect(DATABASE) as db:
        data = []
        for tbl in ["arrests", "citations", "bolos", "warrants"]:
            async with db.execute(f"SELECT COUNT(*) FROM {tbl} WHERE officer_id = ?", (trooper.id,)) as c:
                data.append((await c.fetchone())[0])
        async with db.execute("SELECT COUNT(*) FROM infractions WHERE user_id = ?", (trooper.id,)) as c:
            inf = (await c.fetchone())[0]

    s1, s2, ub = [itx.guild.get_role(ROLES[r]) for r in ['strike_1', 'strike_2', 'up_for_ban']]
    cur = "None"
    if ub in trooper.roles: cur = "⚠️ Up For Termination"
    elif s2 in trooper.roles: cur = "Strike 2"
    elif s1 in trooper.roles: cur = "Strike 1"

    e = discord.Embed(title=f"**PERFORMANCE: {trooper.display_name}**", color=GSP_CUSTOM_ORANGE)
    e.description = f"{SEPARATOR}\n⚡ **Status:** `{cur}`\n🚨 **Arrests:** `{data[0]}`\n🎫 **Citations:** `{data[1]}`\n📡 **BOLOs:** `{data[2]}`\n⚖️ **Warrants:** `{data[3]}`\n⚠️ **Infractions:** `{inf}`\n{SEPARATOR}"
    e.set_footer(text=f"Submitted by {itx.user.display_name}")
    await itx.response.send_message(embed=e)

@bot.tree.command(name='search_record', description='Search any GSP ID')
async def search_record(itx: discord.Interaction, record_id: str):
    if not await is_cmd_channel(itx): return
    rid = record_id.upper()
    async with aiosqlite.connect(DATABASE) as db:
        # Search Arrests
        async with db.execute("SELECT * FROM arrests WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                off = await bot.fetch_user(row[2])
                e = discord.Embed(title="**ARREST RECORD**", color=GSP_CUSTOM_ORANGE)
                e.description = f"**ID:** {row[0]}\n**Officer:** {off.mention}\n**Suspect:** {row[1]}\n**Charges:** {row[4]}\n**Date:** {row[6]}"
                e.set_footer(text=f"Logged by {off.display_name}")
                return await itx.response.send_message(embed=e)
        # Search Citations
        async with db.execute("SELECT * FROM citations WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                off = await bot.fetch_user(row[2])
                e = discord.Embed(title="**CITATION RECORD**", color=GSP_YELLOW)
                e.description = f"**ID:** {row[0]}\n**Officer:** {off.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[5]}\n**Date:** {row[6]}"
                e.set_footer(text=f"Logged by {off.display_name}")
                return await itx.response.send_message(embed=e)
        # Search BOLOs
        async with db.execute("SELECT * FROM bolos WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                off = await bot.fetch_user(row[2])
                e = discord.Embed(title="**BOLO RECORD**", color=GSP_RED)
                e.description = f"**ID:** {row[0]}\n**Officer:** {off.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[6]}"
                e.set_footer(text=f"Logged by {off.display_name}")
                return await itx.response.send_message(embed=e)
        # Search Warrants
        async with db.execute("SELECT * FROM warrants WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                off = await bot.fetch_user(row[2])
                e = discord.Embed(title="**WARRANT RECORD**", color=GSP_RED)
                e.description = f"**ID:** {row[0]}\n**Officer:** {off.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[5]}"
                e.set_footer(text=f"Logged by {off.display_name}")
                return await itx.response.send_message(embed=e)
    await itx.response.send_message(f"❌ `{rid}` not found.", ephemeral=True)

@bot.tree.command(name='infraction_log', description='Log misconduct')
async def infraction_log(itx: discord.Interaction, trooper: discord.Member, reason: str, punishment: str, proof: str = "None"):
    if not await is_cmd_channel(itx): return
    if itx.guild.get_role(ROLES['supervisor']) not in itx.user.roles: return await itx.response.send_message("❌ Restricted.", ephemeral=True)

    async def complete_infraction(itx_select, hours):
        ts = get_pst_time()
        expire_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        e = discord.Embed(title="**INFRACTION LOGGED**", color=GSP_RED)
        e.description = f"**Trooper:** {trooper.mention}\n**Reason:** {reason}\n**Punishment:** {punishment}\n**Proof:** {proof}"
        e.set_footer(text=f"Logged by {itx.user.display_name}")
        log_msg = await bot.get_channel(CHANNELS['infractions']).send(content=f"{trooper.mention}", embed=e)
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('''INSERT INTO infractions (user_id, issuer_id, reason, punishment, proof, msg_url, expiry_timestamp, timestamp) VALUES (?,?,?,?,?,?,?,?)''', (trooper.id, itx.user.id, reason, punishment, proof, log_msg.jump_url, expire_at, ts))
            await db.commit()
            async with db.execute("SELECT id, msg_url FROM infractions WHERE user_id = ? AND is_processed = 0", (trooper.id,)) as c:
                rows = await c.fetchall()
            if len(rows) >= 3:
                s1, s2 = itx.guild.get_role(ROLES['strike_1']), itx.guild.get_role(ROLES['strike_2'])
                next_lvl = "Strike 1"
                if s2 in trooper.roles: next_lvl = "Up For Termination"
                elif s1 in trooper.roles: next_lvl = "Strike 2"
                
                links = "\n".join([f"• [Infraction #{r[0]}]({r[1]})" for r in rows])
                alert = discord.Embed(title="**⚖️ STRIKE ELIGIBILITY ALERT**", color=GSP_RED)
                alert.add_field(name="**Trooper:**", value=trooper.mention, inline=False)
                alert.add_field(name="**Reason:**", value=reason, inline=False)
                alert.add_field(name="**Infractions:**", value=links, inline=False)
                alert.add_field(name="\u200b", value="\u200b", inline=False) # Vertical Space
                alert.add_field(name="**Strike level after confirmation:**", value=f"`{next_lvl}`", inline=False)
                alert.set_footer(text=f"Requested by {itx.user.display_name}")
                await bot.get_channel(CHANNELS['strike_confirm']).send(content=f"{trooper.mention}", embed=alert, view=StrikeConfirmView(trooper, rows))
        await itx_select.response.send_message("✅ Infraction logged.", ephemeral=True)
    await itx.response.send_message("Select Duration:", view=ui.View().add_item(InfractionExpiryDropdown(complete_infraction)), ephemeral=True)

@bot.tree.command(name='search_user', description='NCIC Name Lookup')
async def search_user(itx: discord.Interaction, suspect_name: str):
    if not await is_cmd_channel(itx): return
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        async with db.execute("SELECT id_code, reason FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c: warrants = await c.fetchall()
        async with db.execute("SELECT id_code, reason FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c: bolos = await c.fetchall()
        async with db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (suspect_name,)) as c: last_arrest = await c.fetchone()
    e = discord.Embed(title=f"**NCIC: {suspect_name}**", color=GSP_RED if (warrants or bolos) else discord.Color.green())
    w_t = "\n".join([f"• `{w[0]}`: {w[1]}" for w in warrants]) if warrants else "None"
    b_t = "\n".join([f"• `{b[0]}`: {b[1]}" for b in bolos]) if bolos else "None"
    e.description = f"{SEPARATOR}\n**Warrants:** {w_t}\n**BOLOs:** {b_t}\n**Last Arrest:** {format_time_ago(last_arrest[0]) if last_arrest else 'No priors.'}\n{SEPARATOR}"
    e.set_footer(text=f"Submitted by {itx.user.display_name}")
    await itx.response.send_message(embed=e)

@bot.tree.command(name='arrest_log', description='Record an arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, secondaries: str = "None", mugshot_url: str = "None"):
    if not await is_cmd_channel(itx): return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, secondaries, charges, mugshot_url, ts))
        await db.commit()
    e = discord.Embed(title="**ARREST LOGGED**", color=GSP_CUSTOM_ORANGE)
    e.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Charges:** {charges}"
    if mugshot_url != "None": e.set_image(url=mugshot_url)
    e.set_footer(text=f"Logged by {itx.user.display_name}")
    await bot.get_channel(CHANNELS['arrest_logs']).send(embed=e)
    await itx.response.send_message(f"✅ Logged `{id_code}`", ephemeral=True)

@bot.tree.command(name='citation_log', description='Record a citation')
async def citation_log(itx: discord.Interaction, suspect: str, vehicle: str, location: str, reason: str):
    if not await is_cmd_channel(itx): return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, vehicle, location, reason, ts))
        await db.commit()
    e = discord.Embed(title="**CITATION ISSUED**", color=GSP_YELLOW, description=f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Reason:** {reason}")
    e.set_footer(text=f"Logged by {itx.user.display_name}")
    await bot.get_channel(CHANNELS['citation_logs']).send(embed=e)
    await itx.response.send_message(f"✅ Logged `{id_code}`", ephemeral=True)

@bot.tree.command(name='bolo_log', description='Issue a BOLO')
async def bolo_log(itx: discord.Interaction, suspect: str, vehicle: str, reason: str, plate: str = "Unknown"):
    if not await is_cmd_channel(itx): return
    async def post_bolo(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO bolos VALUES (?,?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, vehicle, plate, expire, ts))
            await db.commit()
        e = discord.Embed(title="**BOLO ACTIVE**", description=f"**ID:** {id_code}\n**Suspect:** {suspect}", color=GSP_RED)
        e.set_footer(text=f"Logged by {itx.user.display_name}")
        await itx_s.response.send_message(embed=e)
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpiryDropdown(post_bolo)), ephemeral=True)

@bot.tree.command(name='warrant_log', description='Issue a warrant')
async def warrant_log(itx: discord.Interaction, suspect: str, reason: str, risk: str = "Medium"):
    if not await is_cmd_channel(itx): return
    async def post_war(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO warrants VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, risk, expire, ts))
            await db.commit()
        e = discord.Embed(title="**WARRANT ACTIVE**", description=f"**ID:** {id_code}\n**Suspect:** {suspect}", color=GSP_RED)
        e.set_footer(text=f"Logged by {itx.user.display_name}")
        await itx_s.response.send_message(embed=e)
    await itx.response.send_message("Duration:", view=ui.View().add_item(ExpiryDropdown(post_war)), ephemeral=True)

@bot.tree.command(name="user_info", description="Discord profile lookup")
async def user_info(itx: discord.Interaction, trooper: discord.Member):
    if not await is_cmd_channel(itx): return
    e = discord.Embed(title=f"**PROFILE: {trooper.display_name}**", color=GSP_CUSTOM_ORANGE)
    e.description = f"{SEPARATOR}\n**ID:** `{trooper.id}`\n**Join Date:** {trooper.joined_at.strftime('%Y-%m-%d')}\n{SEPARATOR}"
    e.set_footer(text=f"Submitted by {itx.user.display_name}")
    await itx.response.send_message(embed=e)

@bot.event
async def on_ready():
    await init_db()
    await bot.tree.sync()
    print("GSP Systems Online.")

bot.run(os.getenv("DISCORD_TOKEN"))