import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiosqlite
from datetime import datetime, timedelta

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)

DATABASE = 'apd_bot.db'

# ---------------------
# CHANNEL IDS
# ---------------------
CHANNELS = {
    'arrest_logs': 1486825085439443125,
    'citation_logs': 1486885813013844148,
    'apd_commands': 1486886286081130526,
    'strike_confirm': 1486824029980463206,
    'promotion_confirm': 1486886126127153313,
    'medal_requests': 1486846567548715189,
    'medal_accept': 1486846829122424994,
    'infractions': 1486847816507719753
}

# ---------------------
# ROLE IDS
# ---------------------
ROLES = {
    'chief_on_duty': 1487053013427290234,
    'assistant_chief_on_duty': 1487052907156213831,
    'deputy_chief_on_duty': 1487052793222266900,
    'detective_on_duty': 1487043416981504142,
    'supervisor_on_duty': 1487053112509468823,
    'fbi_on_duty': 1487052262688948265,
    'fto_on_duty': 1487041827050749964,
    'geu_on_duty': 1487043516650619002,
    'srtf_on_duty': 1487041924413263953,
    'officer_on_duty': 1487041733018521711,
    'up_for_ban': 1486876910905593866,
    'strike_2': 1486876780190105630,
    'strike_1': 1486876700242608268,
    'strike_confirmer': 1486883804550398053,
    'medal_honor': 1486830867279249490,
    'medal_valor': 1486830974330343566,
    'medal_9m': 1486830671681949816,
    'medal_6m': 1486830562655342612,
    'medal_3m': 1486830443222401124,
    'medal_1m': 1486830336842272919
}

SHIFT_TYPES = ['Officer','SWAT','Detective','SRTF','FBI','GEU','Chief of Police','Assistant Chief of Police','Deputy Chief','Supervisor']

# ---------------------
# STARTUP & DB INIT
# ---------------------
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await init_db()
    weekly_reset.start()
    promotion_check.start()
    strike_check.start()
    await bot.tree.sync()
    print('Scheduled tasks started.')

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (user_id INTEGER, officer_id INTEGER, reason TEXT, mugshot TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (user_id INTEGER, officer_id INTEGER, reason TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (user_id INTEGER, officer_id INTEGER, reason TEXT, active INTEGER, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (user_id INTEGER, officer_id INTEGER, reason TEXT, active INTEGER, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS shifts (user_id INTEGER, shift_type TEXT, start_time TEXT, total_minutes INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (user_id INTEGER, officer_id INTEGER, reason TEXT, proof TEXT, notes TEXT, timestamp TEXT)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS strikes (user_id INTEGER, strike_count INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS promotions (user_id INTEGER, consecutive_weeks INTEGER)''')
        await db.execute('''CREATE TABLE IF NOT EXISTS medals (user_id INTEGER, medal_type TEXT, approved INTEGER, timestamp TEXT)''')
        await db.commit()
    print('Database initialized.')

# ---------------------
# SHIFT COMMAND
# ---------------------
class ShiftModal(ui.Modal, title='Start/End Shift'):
    shift_type: ui.TextInput(label='Shift Type (Officer, SWAT, etc.)')

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        if self.shift_type.value not in SHIFT_TYPES:
            await interaction.response.send_message('Invalid shift type.', ephemeral=True)
            return
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('INSERT INTO shifts (user_id, shift_type, start_time, total_minutes) VALUES (?, ?, ?, ?)',
                             (interaction.user.id, self.shift_type.value, ts, 0))
            await db.commit()
        role_id = ROLES.get(f'{self.shift_type.value.lower().replace(" ","_")}_on_duty')
        if role_id:
            role = interaction.guild.get_role(role_id)
            await interaction.user.add_roles(role)
        await interaction.response.send_message(f'{self.shift_type.value} shift started.', ephemeral=True)

@bot.tree.command(name='shift_manage', description='Start/End shift')
async def shift_manage(interaction: discord.Interaction):
    await interaction.response.send_modal(ShiftModal())

# ---------------------
# MEDAL SYSTEM WITH BUTTONS
# ---------------------
class MedalAcceptView(ui.View):
    def __init__(self, user_id: int, medal_type: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.medal_type = medal_type

    @ui.button(label='Approve Medal', style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('UPDATE medals SET approved = 1 WHERE user_id = ? AND medal_type = ?', (self.user_id, self.medal_type))
            await db.commit()
        role = interaction.guild.get_role(ROLES.get(f'medal_{self.medal_type.lower()}'))
        if role:
            await interaction.user.add_roles(role)
        await interaction.response.send_message(f'Medal {self.medal_type} approved for <@{self.user_id}>', ephemeral=False)

class MedalModal(ui.Modal, title='Request Medal'):
    medal_type: ui.TextInput(label='Medal Type')
    reason: ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)
    proof: ui.TextInput(label='Proof URL (optional)', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('INSERT INTO medals VALUES (?, ?, 0, ?)', (interaction.user.id, self.medal_type.value, ts))
            await db.commit()
        embed = discord.Embed(title='Medal Request', color=0x000c77)
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Reason', value=self.reason.value)
        embed.add_field(name='Proof', value=self.proof.value or 'N/A')
        channel = bot.get_channel(CHANNELS['medal_requests'])
        await channel.send(embed=embed, view=MedalAcceptView(interaction.user.id, self.medal_type.value))
        await interaction.response.send_message('Medal request submitted!', ephemeral=True)

@bot.tree.command(name='request_medal', description='Request a medal')
async def request_medal(interaction: discord.Interaction):
    await interaction.response.send_modal(MedalModal())

# ---------------------
# STRIKE CONFIRMATION BUTTON WITH AUTOMATION
# ---------------------
class StrikeConfirmView(ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

    @ui.button(label='Confirm Strike', style=discord.ButtonStyle.danger)
    async def confirm_strike(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DATABASE) as db:
            cursor = await db.execute('SELECT strike_count FROM strikes WHERE user_id = ?', (self.user_id,))
            row = await cursor.fetchone()
            if row:
                count = row[0] + 1
                await db.execute('UPDATE strikes SET strike_count = ? WHERE user_id = ?', (count, self.user_id))
            else:
                count = 1
                await db.execute('INSERT INTO strikes VALUES (?, ?)', (self.user_id, count))
            await db.commit()
        guild = interaction.guild
        member = guild.get_member(self.user_id)
        # Assign strike roles and Up for Ban automatically
        if count == 1 and member:
            await member.add_roles(guild.get_role(ROLES['strike_1']))
        elif count == 2 and member:
            await member.remove_roles(guild.get_role(ROLES['strike_1']))
            await member.add_roles(guild.get_role(ROLES['strike_2']))
        elif count >= 3 and member:
            await member.remove_roles(guild.get_role(ROLES['strike_2']))
            await member.add_roles(guild.get_role(ROLES['up_for_ban']))
        infra_channel = bot.get_channel(CHANNELS['infractions'])
        await infra_channel.send(f'<@{self.user_id}> now has {count} strike(s).')
        await interaction.response.send_message(f'Strike confirmed for <@{self.user_id}>', ephemeral=False)

# ---------------------
# WEEKLY RESET TASK
# ---------------------
@tasks.loop(hours=24)
async def weekly_reset():
    now = datetime.utcnow()
    if now.weekday() == 4 and now.hour >= 21:  # Friday 5 PM EST
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute('UPDATE shifts SET total_minutes = 0')
            await db.execute('UPDATE promotions SET consecutive_weeks = 0')
            await db.commit()
        print('Weekly reset complete.')

# ---------------------
# PROMOTION CHECK TASK
# ---------------------
@tasks.loop(minutes=60)
async def promotion_check():
    async with aiosqlite.connect(DATABASE) as db:
        # Implement logic for automatic and SHR-approved promotions
        pass

# ---------------------
# STRIKE CHECK TASK
# ---------------------
@tasks.loop(minutes=60)
async def strike_check():
    async with aiosqlite.connect(DATABASE) as db:
        # Implement logic for checking infractions and assigning strikes automatically
        pass
import os

bot.run(os.environ['DISCORD_TOKEN'])