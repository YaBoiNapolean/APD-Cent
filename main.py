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
    utc_now = datetime.utcnow()
    pst_now = utc_now - timedelta(hours=8)
    return pst_now.strftime('%B %d, %Y at %H:%M')

def format_time_ago(ts_string):
    """Calculates how long ago a timestamp occurred for NCIC reports."""
    try:
        past = datetime.strptime(ts_string, '%B %d, %Y at %H:%M')
        now = datetime.utcnow() - timedelta(hours=8)
        diff = now - past
        
        if diff.days > 0:
            return f"{diff.days} days ago"
        
        hours = diff.seconds // 3600
        if hours > 0:
            return f"{hours} hours ago"
            
        minutes = diff.seconds // 60
        return f"{minutes} minutes ago"
    except Exception:
        return "Unknown"

async def init_db():
    """Initializes all required database tables for the GSP System."""
    async with aiosqlite.connect(DATABASE) as db:
        # Table for permanent Arrest records
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            secondaries TEXT, charges TEXT, mugshot TEXT, timestamp TEXT)''')
        
        # Table for permanent Citation records
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            vehicle TEXT, location TEXT, reason TEXT, timestamp TEXT)''')
        
        # Table for temporary BOLOs
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            reason TEXT, vehicle TEXT, plate TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        
        # Table for temporary Warrants
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (
            id_code TEXT PRIMARY KEY, suspect TEXT, officer_id INTEGER, 
            reason TEXT, risk_level TEXT, expiry_timestamp TEXT, timestamp TEXT)''')
        
        # Table for Internal Trooper Infractions
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, issuer_id INTEGER, 
            reason TEXT, punishment TEXT, proof TEXT, msg_url TEXT, is_active INTEGER DEFAULT 1, 
            is_processed INTEGER DEFAULT 0, expiry_timestamp TEXT, timestamp TEXT)''')
            
        await db.commit()

async def generate_unique_id():
    """Generates a unique GSP-XXXX ID for records and ensures no collisions."""
    async with aiosqlite.connect(DATABASE) as db:
        while True:
            random_digits = ''.join(random.choices(string.digits, k=4))
            new_id = f"GSP{random_digits}"
            
            # Check across all tables to ensure ID is truly unique
            query = "SELECT 1 FROM arrests WHERE id_code = ? UNION SELECT 1 FROM bolos WHERE id_code = ?"
            async with db.execute(query, (new_id, new_id)) as cursor:
                if not await cursor.fetchone():
                    return new_id

async def is_cmd_channel(itx: discord.Interaction):
    """Restricts bot usage to the designated GSP Commands channel."""
    if itx.channel.id != CMD_CHANNEL_ID:
        error_msg = f"❌ This command is restricted to <#{CMD_CHANNEL_ID}>."
        await itx.response.send_message(error_msg, ephemeral=True)
        return False
    return True

# --- UI COMPONENTS ---

class StrikeConfirmView(ui.View):
    """View sent to supervisors to confirm a Strike after 3 infractions."""
    def __init__(self, trooper: discord.Member, infraction_ids: list):
        super().__init__(timeout=None)
        self.trooper = trooper
        self.infraction_ids = infraction_ids

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.success)
    async def confirm_strike(self, itx: discord.Interaction, button: ui.Button):
        # Check if the user has the 'Strike Confirmer' role
        confirmer_role = itx.guild.get_role(ROLES['strike_confirmer'])
        if confirmer_role not in itx.user.roles:
            return await itx.response.send_message("❌ Unauthorized: Missing Strike Confirmer role.", ephemeral=True)
            
        s1 = itx.guild.get_role(ROLES['strike_1'])
        s2 = itx.guild.get_role(ROLES['strike_2'])
        ub = itx.guild.get_role(ROLES['up_for_ban'])
        
        # Determine the next strike level
        target_role = s1
        if ub in self.trooper.roles:
            return await itx.response.send_message("⚠️ Trooper is already at the maximum Strike level (Up For Ban).", ephemeral=True)
        elif s2 in self.trooper.roles:
            target_role = ub
        elif s1 in self.trooper.roles:
            target_role = s2
        
        # Apply the role
        await self.trooper.add_roles(target_role)
        
        # Mark these infractions as 'processed' so they don't trigger another strike alert
        async with aiosqlite.connect(DATABASE) as db:
            for inf_id in self.infraction_ids:
                await db.execute("UPDATE infractions SET is_processed = 1 WHERE id = ?", (inf_id,))
            await db.commit()

        # Log the confirmation
        log_embed = discord.Embed(title="Strike Action Confirmed", color=GSP_RED)
        log_embed.description = f"{SEPARATOR}\n**Trooper:** {self.trooper.mention}\n**New Status:** {target_role.mention}\n{SEPARATOR}"
        
        inf_channel = bot.get_channel(CHANNELS['infractions'])
        if inf_channel:
            await inf_channel.send(content=f"{self.trooper.mention}", embed=log_embed)

        await itx.response.edit_message(content="✅ Strike successfully applied.", embed=log_embed, view=None)

class ExpiryDropdown(ui.Select):
    """Standard duration selector for BOLOs and Warrants."""
    def __init__(self, callback_func):
        options = [
            discord.SelectOption(label="24 Hours", value="24"),
            discord.SelectOption(label="48 Hours", value="48"),
            discord.SelectOption(label="72 Hours", value="72"),
            discord.SelectOption(label="1 Week", value="168"),
        ]
        super().__init__(placeholder="Select Duration...", options=options)
        self.callback_func = callback_func

    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class InfractionExpiryDropdown(ui.Select):
    """Extended duration selector specifically for Trooper Infractions."""
    def __init__(self, callback_func):
        options = [
            discord.SelectOption(label="24 Hours", value="24"),
            discord.SelectOption(label="48 Hours", value="48"),
            discord.SelectOption(label="72 Hours", value="72"),
            discord.SelectOption(label="1 Week", value="168"),
            discord.SelectOption(label="2 Weeks", value="336"),
            discord.SelectOption(label="3 Weeks", value="504"),
            discord.SelectOption(label="1 Month", value="720"),
            discord.SelectOption(label="1.5 Months", value="1080"),
            discord.SelectOption(label="2 Months", value="1440"),
        ]
        super().__init__(placeholder="Select Infraction Duration...", options=options)
        self.callback_func = callback_func

    async def callback(self, itx: discord.Interaction):
        await self.callback_func(itx, int(self.values[0]))

class ClearRecordConfirm(ui.View):
    """Verification menu for record deletion."""
    def __init__(self, original_user, officer_id, record_id, table):
        super().__init__(timeout=60)
        self.original_user = original_user
        self.officer_id = int(officer_id)
        self.record_id = record_id
        self.table = table

    @ui.button(label="Confirm Deletion", style=discord.ButtonStyle.danger)
    async def confirm_delete(self, itx: discord.Interaction, button: ui.Button):
        if itx.user.id != self.original_user.id:
            return await itx.response.send_message("❌ This interaction belongs to someone else.", ephemeral=True)
        
        is_supervisor = itx.guild.get_role(ROLES['supervisor']) in itx.user.roles
        if itx.user.id != self.officer_id and not is_supervisor:
            return await itx.response.send_message("❌ Permission Denied: You are not the issuing officer or a supervisor.", ephemeral=True)

        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(f"DELETE FROM {self.table} WHERE id_code = ?", (self.record_id,))
            await db.commit()
            
        await itx.response.send_message(f"🗑️ Record `{self.record_id}` has been purged from the `{self.table}` database.", ephemeral=True)
        await itx.message.delete()

# --- COMMANDS ---

@bot.tree.command(name='info', description='Bot support and contact info')
async def info(itx: discord.Interaction):
    """Simple command to display contact info for bugs/questions."""
    if not await is_cmd_channel(itx):
        return
        
    info_embed = discord.Embed(
        description="If you have any questions or find any bugs, please DM **YaBoi_Napolean**.",
        color=GSP_CUSTOM_ORANGE
    )
    await itx.response.send_message(embed=info_embed)

@bot.tree.command(name='trooper_performance', description='View trooper lifetime stats and strike status')
async def trooper_performance(itx: discord.Interaction, trooper: discord.Member):
    """Pulls all-time stats for a trooper and checks their current strike role."""
    if not await is_cmd_channel(itx):
        return
    
    async with aiosqlite.connect(DATABASE) as db:
        # Get counts for every category
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

    s1 = itx.guild.get_role(ROLES['strike_1'])
    s2 = itx.guild.get_role(ROLES['strike_2'])
    ub = itx.guild.get_role(ROLES['up_for_ban'])
    
    status = "None"
    if ub in trooper.roles:
        status = "⚠️ Up For Ban"
    elif s2 in trooper.roles:
        status = "Strike 2"
    elif s1 in trooper.roles:
        status = "Strike 1"

    perf_embed = discord.Embed(title=f"📈 TROOPER PERFORMANCE: {trooper.display_name}", color=GSP_CUSTOM_ORANGE)
    perf_embed.set_thumbnail(url=trooper.display_avatar.url)
    perf_embed.description = (
        f"{SEPARATOR}\n\n"
        f"⚡ **Current Status:** `{status}`\n\n"
        f"🚨 **Arrests:** `{arr_count}`\n"
        f"🎫 **Citations:** `{cit_count}`\n"
        f"📡 **BOLOs:** `{blo_count}`\n"
        f"⚖️ **Warrants:** `{war_count}`\n"
        f"⚠️ **Infractions:** `{inf_count}`\n\n"
        f"{SEPARATOR}"
    )
    perf_embed.set_footer(text=f"Last Sync: {get_pst_time()}")
    await itx.response.send_message(embed=perf_embed)

@bot.tree.command(name='search_record', description='Lookup details of a specific GSP ID')
async def search_record(itx: discord.Interaction, record_id: str):
    """Scans all database tables to find and display the details of an ID."""
    if not await is_cmd_channel(itx):
        return
    
    rid = record_id.upper()
    async with aiosqlite.connect(DATABASE) as db:
        # Check Arrests
        async with db.execute("SELECT * FROM arrests WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="🚨 ARREST RECORD FOUND", color=GSP_CUSTOM_ORANGE)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Charges:** {row[4]}\n**Date:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)
        
        # Check Citations
        async with db.execute("SELECT * FROM citations WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="🎫 CITATION RECORD FOUND", color=GSP_YELLOW)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[5]}\n**Date:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

        # Check BOLOs
        async with db.execute("SELECT * FROM bolos WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="📡 BOLO RECORD FOUND", color=GSP_RED)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[6]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

        # Check Warrants
        async with db.execute("SELECT * FROM warrants WHERE id_code = ?", (rid,)) as c:
            row = await c.fetchone()
            if row:
                officer = await bot.fetch_user(row[2])
                e = discord.Embed(title="⚖️ WARRANT RECORD FOUND", color=GSP_RED)
                e.description = f"{SEPARATOR}\n**ID:** {row[0]}\n**Officer:** {officer.mention}\n**Suspect:** {row[1]}\n**Reason:** {row[3]}\n**Expires:** {row[5]}\n{SEPARATOR}"
                return await itx.response.send_message(embed=e)

    await itx.response.send_message(f"❌ Error: ID `{rid}` does not exist in the system.", ephemeral=True)

@bot.tree.command(name='clear_record', description='Delete a record by ID (Officer/Supervisor only)')
async def clear_record(itx: discord.Interaction, record_id: str):
    """Finds a record and prompts for deletion if the user is authorized."""
    if not await is_cmd_channel(itx):
        return
    rid = record_id.upper()
    async with aiosqlite.connect(DATABASE) as db:
        for table in ["arrests", "citations", "bolos", "warrants"]:
            async with db.execute(f"SELECT officer_id FROM {table} WHERE id_code = ?", (rid,)) as c:
                row = await c.fetchone()
                if row:
                    view = ClearRecordConfirm(itx.user, row[0], rid, table)
                    return await itx.response.send_message(f"🚨 Record `{rid}` found in `{table}`. Do you wish to delete it?", view=view, ephemeral=True)
    await itx.response.send_message("❌ ID not found.", ephemeral=True)

@bot.tree.command(name='infraction_log', description='Log misconduct (Supervisor Only)')
async def infraction_log(itx: discord.Interaction, trooper: discord.Member, reason: str, punishment: str, proof: str = "None"):
    """Logs an internal infraction with the newly expanded expiration options."""
    if not await is_cmd_channel(itx):
        return
    if itx.guild.get_role(ROLES['supervisor']) not in itx.user.roles:
        return await itx.response.send_message("❌ Access Restricted: Supervisors only.", ephemeral=True)

    async def complete_infraction(itx_select, hours):
        timestamp = get_pst_time()
        expire_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        
        inf_embed = discord.Embed(title="⚠️ INFRACTION LOGGED", color=GSP_RED)
        inf_embed.description = (
            f"**Trooper:** {trooper.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Punishment:** {punishment}\n"
            f"**Proof:** {proof}"
        )
        
        # Log to infraction channel
        log_msg = await bot.get_channel(CHANNELS['infractions']).send(content=f"{trooper.mention}", embed=inf_embed)
        
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('''INSERT INTO infractions (user_id, issuer_id, reason, punishment, proof, msg_url, expiry_timestamp, timestamp) 
                             VALUES (?,?,?,?,?,?,?,?)''', (trooper.id, itx.user.id, reason, punishment, proof, log_msg.jump_url, expire_at, timestamp))
            await db.commit()
            
            # Check for 3 active, unprocessed infractions
            async with db.execute("SELECT id FROM infractions WHERE user_id = ? AND is_processed = 0", (trooper.id,)) as c:
                active_rows = await c.fetchall()
                
            if len(active_rows) >= 3:
                inf_ids = [r[0] for r in active_rows]
                alert_embed = discord.Embed(title="⚖️ STRIKE ELIGIBILITY ALERT", color=GSP_RED)
                alert_embed.description = f"{trooper.mention} has accumulated 3 active infractions and is eligible for a strike."
                await bot.get_channel(CHANNELS['strike_confirm']).send(content=f"{trooper.mention}", embed=alert_embed, view=StrikeConfirmView(trooper, inf_ids))
        
        await itx_select.response.send_message("✅ Infraction has been logged.", ephemeral=True)

    # Use the new InfractionExpiryDropdown class
    view = ui.View().add_item(InfractionExpiryDropdown(complete_infraction))
    await itx.response.send_message("Select the expiration duration for this infraction:", view=view, ephemeral=True)

@bot.tree.command(name='search_user', description='NCIC lookup for suspect profile')
async def search_user(itx: discord.Interaction, suspect_name: str):
    """NCIC profile lookup showing warrants, BOLOs, and last arrest."""
    if not await is_cmd_channel(itx):
        return
    now = datetime.utcnow().isoformat()
    async with aiosqlite.connect(DATABASE) as db:
        # Get Active Warrants
        async with db.execute("SELECT id_code, reason FROM warrants WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c:
            warrants = await c.fetchall()
        # Get Active BOLOs
        async with db.execute("SELECT id_code, reason FROM bolos WHERE suspect = ? AND expiry_timestamp > ?", (suspect_name, now)) as c:
            bolos = await c.fetchall()
        # Get Last Arrest
        async with db.execute("SELECT timestamp FROM arrests WHERE suspect = ? ORDER BY timestamp DESC LIMIT 1", (suspect_name,)) as c:
            last_arrest = await c.fetchone()

    status_color = GSP_RED if (warrants or bolos) else discord.Color.green()
    ncic_embed = discord.Embed(title=f"🔍 NCIC FILE: {suspect_name}", color=status_color)
    
    w_text = "\n".join([f"• `{w[0]}`: {w[1]}" for w in warrants]) if warrants else "No Active Warrants"
    b_text = "\n".join([f"• `{b[0]}`: {b[1]}" for b in bolos]) if bolos else "No Active BOLOs"
    arrest_text = format_time_ago(last_arrest[0]) if last_arrest else "No record of prior arrest."

    ncic_embed.description = (
        f"{SEPARATOR}\n\n"
        f"⚖️ **Active Warrants:**\n{w_text}\n\n"
        f"📡 **Active BOLOs:**\n{b_text}\n\n"
        f"⛓️ **Last Arrest:**\n{arrest_text}\n\n"
        f"{SEPARATOR}"
    )
    await itx.response.send_message(embed=ncic_embed)

@bot.tree.command(name='arrest_log', description='Log a suspect arrest')
async def arrest_log(itx: discord.Interaction, suspect: str, charges: str, secondaries: str = "None", mugshot_url: str = "None"):
    """Permanent log for suspect arrests."""
    if not await is_cmd_channel(itx):
        return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO arrests VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, secondaries, charges, mugshot_url, ts))
        await db.commit()
    
    log_embed = discord.Embed(title="🚨 ARREST LOGGED", color=GSP_CUSTOM_ORANGE)
    log_embed.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Officer:** {itx.user.mention}\n**Charges:** {charges}"
    if mugshot_url != "None":
        log_embed.set_image(url=mugshot_url)
        
    await bot.get_channel(CHANNELS['arrest_logs']).send(embed=log_embed)
    await itx.response.send_message(f"✅ Arrest logged as `{id_code}`", ephemeral=True)

@bot.tree.command(name='citation_log', description='Log a citation')
async def citation_log(itx: discord.Interaction, suspect: str, vehicle: str, location: str, reason: str):
    """Permanent log for citations/tickets."""
    if not await is_cmd_channel(itx):
        return
    id_code, ts = await generate_unique_id(), get_pst_time()
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute("INSERT INTO citations VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, vehicle, location, reason, ts))
        await db.commit()
    
    cite_embed = discord.Embed(title="🎫 CITATION ISSUED", color=GSP_YELLOW)
    cite_embed.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Vehicle:** {vehicle}\n**Reason:** {reason}"
    
    await bot.get_channel(CHANNELS['citation_logs']).send(embed=cite_embed)
    await itx.response.send_message(f"✅ Citation `{id_code}` recorded.", ephemeral=True)

@bot.tree.command(name='bolo_log', description='Issue a Be On The Lookout')
async def bolo_log(itx: discord.Interaction, suspect: str, vehicle: str, reason: str, plate: str = "Unknown"):
    """BOLO issuer with standard short-term duration options."""
    if not await is_cmd_channel(itx):
        return

    async def post_bolo(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO bolos VALUES (?,?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, vehicle, plate, expire, ts))
            await db.commit()
        
        bolo_embed = discord.Embed(title="📡 BOLO BROADCAST", color=GSP_RED)
        bolo_embed.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Vehicle:** {vehicle}\n**Reason:** {reason}"
        await itx_s.response.send_message(embed=bolo_embed)

    view = ui.View().add_item(ExpiryDropdown(post_bolo))
    await itx.response.send_message("Set BOLO duration:", view=view, ephemeral=True)

@bot.tree.command(name='warrant_log', description='Issue a criminal warrant')
async def warrant_log(itx: discord.Interaction, suspect: str, reason: str, risk: str = "Medium"):
    """Warrant issuer with standard short-term duration options."""
    if not await is_cmd_channel(itx):
        return

    async def post_warrant(itx_s, hours):
        id_code, ts, expire = await generate_unique_id(), get_pst_time(), (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute("INSERT INTO warrants VALUES (?,?,?,?,?,?,?)", (id_code, suspect, itx.user.id, reason, risk, expire, ts))
            await db.commit()
            
        warrant_embed = discord.Embed(title="⚖️ WARRANT ACTIVE", color=GSP_RED)
        warrant_embed.description = f"**ID:** {id_code}\n**Suspect:** {suspect}\n**Reason:** {reason}\n**Risk Level:** {risk}"
        await itx_s.response.send_message(embed=warrant_embed)

    view = ui.View().add_item(ExpiryDropdown(post_warrant))
    await itx.response.send_message("Set Warrant duration:", view=view, ephemeral=True)

@bot.tree.command(name="user_info", description="View basic trooper account info")
async def user_info(itx: discord.Interaction, trooper: discord.Member):
    """Shows Discord profile data and server join date."""
    if not await is_cmd_channel(itx):
        return
    info_embed = discord.Embed(title=f"👤 TROOPER PROFILE: {trooper.display_name}", color=GSP_CUSTOM_ORANGE)
    info_embed.description = (
        f"{SEPARATOR}\n"
        f"**Account ID:** `{trooper.id}`\n"
        f"**Top Role:** {trooper.top_role.mention}\n"
        f"**Join Date:** {trooper.joined_at.strftime('%Y-%m-%d')}\n"
        f"{SEPARATOR}"
    )
    await itx.response.send_message(embed=info_embed)

@bot.event
async def on_ready():
    """Bot initialization event."""
    await init_db()
    await bot.tree.sync()
    print("GSP Systems Fully Operational.")

bot.run(os.getenv("DISCORD_TOKEN"))