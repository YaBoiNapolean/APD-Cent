import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import aiosqlite
from datetime import datetime
import os

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
    'medal_requests': 1486846567548715189,
    'medal_accept': 1486846829122424994,
    'infractions': 1486847816507719753
}

# ---------------------
# ROLE IDS
# ---------------------
ROLES = {
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

# ---------------------
# STARTUP & DB INIT
# ---------------------
@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    await init_db()
    await bot.tree.sync()
    print('Bot ready.')

async def init_db():
    async with aiosqlite.connect(DATABASE) as db:
        await db.execute('''CREATE TABLE IF NOT EXISTS arrests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            officer_id INTEGER,
            reason TEXT,
            mugshot TEXT,
            timestamp TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            officer_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS warrants (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            officer_id INTEGER,
            reason TEXT,
            active INTEGER DEFAULT 1,
            timestamp TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS bolos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            officer_id INTEGER,
            description TEXT,
            active INTEGER DEFAULT 1,
            timestamp TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS infractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            officer_id INTEGER,
            reason TEXT,
            proof TEXT,
            timestamp TEXT
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS strikes (
            user_id INTEGER PRIMARY KEY,
            strike_count INTEGER DEFAULT 0
        )''')
        await db.execute('''CREATE TABLE IF NOT EXISTS medals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            medal_type TEXT,
            approved INTEGER DEFAULT 0,
            timestamp TEXT
        )''')
        await db.commit()
    print('Database initialized.')

# ---------------------
# ARREST LOG
# ---------------------
class ArrestModal(ui.Modal, title='Log Arrest'):
    suspect = ui.TextInput(label='Suspect Name/ID')
    reason = ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)
    mugshot = ui.TextInput(label='Mugshot URL (optional)', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO arrests (user_id, officer_id, reason, mugshot, timestamp) VALUES (?, ?, ?, ?, ?)',
                (0, interaction.user.id, self.reason.value, self.mugshot.value or 'N/A', ts)
            )
            await db.commit()
        embed = discord.Embed(title='Arrest Logged', color=discord.Color.red())
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Suspect', value=self.suspect.value)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.add_field(name='Mugshot', value=self.mugshot.value or 'N/A', inline=False)
        embed.set_footer(text=ts)
        channel = bot.get_channel(CHANNELS['arrest_logs'])
        await channel.send(embed=embed)
        await interaction.response.send_message('Arrest logged!', ephemeral=True)

@bot.tree.command(name='arrest_log', description='Log an arrest')
async def arrest_log(interaction: discord.Interaction):
    await interaction.response.send_modal(ArrestModal())

# ---------------------
# CITATION LOG
# ---------------------
class CitationModal(ui.Modal, title='Submit Citation'):
    suspect = ui.TextInput(label='Suspect Name/ID')
    reason = ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO citations (user_id, officer_id, reason, timestamp) VALUES (?, ?, ?, ?)',
                (0, interaction.user.id, self.reason.value, ts)
            )
            await db.commit()
        embed = discord.Embed(title='Citation Submitted', color=discord.Color.blue())
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Suspect', value=self.suspect.value)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.set_footer(text=ts)
        channel = bot.get_channel(CHANNELS['citation_logs'])
        await channel.send(embed=embed)
        await interaction.response.send_message('Citation submitted!', ephemeral=True)

@bot.tree.command(name='citation_submit', description='Submit a citation')
async def citation_submit(interaction: discord.Interaction):
    await interaction.response.send_modal(CitationModal())

# ---------------------
# WARRANT
# ---------------------
class WarrantModal(ui.Modal, title='Submit Warrant'):
    suspect = ui.TextInput(label='Suspect Name/ID')
    reason = ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO warrants (user_id, officer_id, reason, timestamp) VALUES (?, ?, ?, ?)',
                (0, interaction.user.id, self.reason.value, ts)
            )
            await db.commit()
        embed = discord.Embed(title='Warrant Submitted', color=discord.Color.orange())
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Suspect', value=self.suspect.value)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.set_footer(text=ts)
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name='warrant_submit', description='Submit a warrant')
async def warrant_submit(interaction: discord.Interaction):
    await interaction.response.send_modal(WarrantModal())

# ---------------------
# BOLO
# ---------------------
class BoloModal(ui.Modal, title='Submit BOLO'):
    description = ui.TextInput(label='Description', style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO bolos (officer_id, description, timestamp) VALUES (?, ?, ?)',
                (interaction.user.id, self.description.value, ts)
            )
            await db.commit()
        embed = discord.Embed(title='BOLO Submitted', color=discord.Color.gold())
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Description', value=self.description.value, inline=False)
        embed.set_footer(text=ts)
        await interaction.response.send_message(embed=embed)

@bot.tree.command(name='bolo_submit', description='Submit a BOLO')
async def bolo_submit(interaction: discord.Interaction):
    await interaction.response.send_modal(BoloModal())

# ---------------------
# INFRACTION / STRIKE
# ---------------------
class StrikeConfirmView(ui.View):
    def __init__(self, user_id: int, reason: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.reason = reason

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

        if member:
            if count == 1:
                await member.add_roles(guild.get_role(ROLES['strike_1']))
            elif count == 2:
                await member.remove_roles(guild.get_role(ROLES['strike_1']))
                await member.add_roles(guild.get_role(ROLES['strike_2']))
            elif count >= 3:
                await member.remove_roles(guild.get_role(ROLES['strike_2']))
                await member.add_roles(guild.get_role(ROLES['up_for_ban']))

        infra_channel = bot.get_channel(CHANNELS['infractions'])
        await infra_channel.send(f'<@{self.user_id}> now has {count} strike(s). Reason: {self.reason}')
        await interaction.response.edit_message(content=f'Strike confirmed for <@{self.user_id}>', view=None)

    @ui.button(label='Cancel', style=discord.ButtonStyle.gray)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content='Strike cancelled.', view=None)

class InfractionModal(ui.Modal, title='Log Infraction'):
    user_id = ui.TextInput(label='Officer User ID')
    reason = ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)
    proof = ui.TextInput(label='Proof URL (optional)', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            uid = int(self.user_id.value)
        except ValueError:
            await interaction.response.send_message('Invalid user ID.', ephemeral=True)
            return

        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO infractions (user_id, officer_id, reason, proof, timestamp) VALUES (?, ?, ?, ?, ?)',
                (uid, interaction.user.id, self.reason.value, self.proof.value or 'N/A', ts)
            )
            await db.commit()

        embed = discord.Embed(title='Infraction - Strike Pending', color=discord.Color.orange())
        embed.add_field(name='Officer', value=f'<@{uid}>')
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.add_field(name='Proof', value=self.proof.value or 'N/A', inline=False)
        await interaction.response.send_message(
            embed=embed,
            view=StrikeConfirmView(uid, self.reason.value)
        )

@bot.tree.command(name='infraction_log', description='Log an infraction and issue a strike')
async def infraction_log(interaction: discord.Interaction):
    await interaction.response.send_modal(InfractionModal())

# ---------------------
# MEDAL SYSTEM
# ---------------------
class MedalAcceptView(ui.View):
    def __init__(self, user_id: int, medal_type: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.medal_type = medal_type

    @ui.button(label='Approve Medal', style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: ui.Button):
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'UPDATE medals SET approved = 1 WHERE user_id = ? AND medal_type = ?',
                (self.user_id, self.medal_type)
            )
            await db.commit()
        role = interaction.guild.get_role(ROLES.get(f'medal_{self.medal_type.lower()}'))
        if role:
            member = interaction.guild.get_member(self.user_id)
            if member:
                await member.add_roles(role)
        await interaction.response.edit_message(
            content=f'Medal {self.medal_type} approved for <@{self.user_id}>',
            view=None
        )

    @ui.button(label='Deny Medal', style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content=f'Medal denied for <@{self.user_id}>',
            view=None
        )

class MedalModal(ui.Modal, title='Request Medal'):
    medal_type = ui.TextInput(label='Medal Type (honor, valor, 1m, 3m, 6m, 9m)')
    reason = ui.TextInput(label='Reason', style=discord.TextStyle.paragraph)
    proof = ui.TextInput(label='Proof URL (optional)', required=False)

    async def on_submit(self, interaction: discord.Interaction):
        ts = datetime.utcnow().isoformat()
        async with aiosqlite.connect(DATABASE) as db:
            await db.execute(
                'INSERT INTO medals (user_id, medal_type, timestamp) VALUES (?, ?, ?)',
                (interaction.user.id, self.medal_type.value, ts)
            )
            await db.commit()
        embed = discord.Embed(title='Medal Request', color=0x000c77)
        embed.add_field(name='Officer', value=interaction.user.mention)
        embed.add_field(name='Medal', value=self.medal_type.value)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.add_field(name='Proof', value=self.proof.value or 'N/A', inline=False)
        channel = bot.get_channel(CHANNELS['medal_requests'])
        await channel.send(embed=embed, view=MedalAcceptView(interaction.user.id, self.medal_type.value))
        await interaction.response.send_message('Medal request submitted!', ephemeral=True)

@bot.tree.command(name='request_medal', description='Request a medal')
async def request_medal(interaction: discord.Interaction):
    await interaction.response.send_modal(MedalModal())

bot.run(os.environ['DISCORD_TOKEN'])