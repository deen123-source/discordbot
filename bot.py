import os
import sys
import re
import asyncio
import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp
from aiohttp import web
import static_ffmpeg
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

# ==========================================
# 0. บังคับ Encoding UTF-8 ทั้งระบบ
# ==========================================
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

static_ffmpeg.add_paths()

# ==========================================
# 0.1 Keep-Alive Web Server สำหรับ Render
# ==========================================
async def handle_health_check(request):
    return web.Response(text="Bot is running perfectly!")

async def start_dummy_web_server():
    app = web.Application()
    app.router.add_get("/", handle_health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ==========================================
# 1. ตั้งค่า Bot Client & Spotify API
# ==========================================
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

SPOTIPY_CLIENT_ID = os.getenv("SPOTIPY_CLIENT_ID", "")
SPOTIPY_CLIENT_SECRET = os.getenv("SPOTIPY_CLIENT_SECRET", "")

sp = None
if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIPY_CLIENT_ID,
            client_secret=SPOTIPY_CLIENT_SECRET
        ))
        print("เชื่อมต่อ Spotify API สำเร็จ!")
    except Exception as e:
        print(f"เชื่อมต่อ Spotify API ไม่สำเร็จ: {e}")

class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        print("กำลัง Sync Commands...")
        await self.tree.sync()
        print("Sync Slash Commands เรียบร้อยแล้ว!")

bot = MyBot()

# ==========================================
# 2. Global Configuration Data
# ==========================================
config = {
    "welcome_channel_id": None,
    "goodbye_channel_id": None,
    "welcome_message": "ยินดีต้อนรับ {member} เข้าสู่เซิร์ฟเวอร์!",
    "goodbye_message": "คุณ {member} ได้ออกจากเซิร์ฟเวอร์ไปแล้ว...",
    "welcome_image_url": "",
    "verify_title": "📌 ยืนยันตัวตน",
    "verify_desc": "ยินดีต้อนรับเข้าสู่เซิร์ฟเวอร์! กรุณากดปุ่มด้านล่างเพื่อทำการยืนยันตัวตนและรับยศครับ",
    "verify_role_id": None,
    "verify_log_channel_id": None,
    "verify_questions": [
        {"label": "อยากแรกเลยนะ ชื่อเล่นชื่ออะไรหรออออ", "placeholder": "กรอกชื่อเล่นตรงนี้นะ", "required": True},
        {"label": "อายุเท่าไหร่ยยย", "placeholder": "ไม่อยากบอกก็ได้นะ :(", "required": False},
        {"label": "ได้ดิสจากไหนหรอจ้ะ", "placeholder": "บอกหน่อยน้า", "required": True}
    ]
}

# ==========================================
# 3. Audio Extraction Helper Functions
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1:',
    'source_address': '0.0.0.0',
    'nocheckcertificate': True,
    'ignoreerrors': True,
    'geo_bypass': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['ios', 'android', 'mweb']
        }
    },
    'headers': {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
    }
}

if os.path.exists('cookies.txt'):
    YTDL_OPTIONS['cookiefile'] = 'cookies.txt'

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# ==========================================
# 4. Music Player & Controller
# ==========================================
class MusicPlayer:
    def __init__(self, interaction):
        self.bot = interaction.client
        self.guild = interaction.guild
        self.channel = interaction.channel

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.current = None
        self.is_looping = False
        self.panel_message = None
        self._updating = False

        self.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            if not self.is_looping or not self.current:
                try:
                    async with asyncio.timeout(300):
                        self.current = await self.queue.get()
                except TimeoutError:
                    return self.destroy(self.guild)

            try:
                source = discord.FFmpegPCMAudio(
                    self.current['url'],
                    executable='ffmpeg',
                    **FFMPEG_OPTIONS
                )
                self.guild.voice_client.play(source, after=lambda e: self.bot.loop.call_soon_threadsafe(self.next.set))
                await self.update_panel()
                await self.next.wait()
                source.cleanup()
            except Exception as e:
                print(f"Play error: {e}")
                await self.next.wait()

    async def update_panel(self):
        if not self.current or self._updating:
            return
        
        self._updating = True
        try:
            embed = discord.Embed(title="🎶 Music Control Panel", color=discord.Color.purple())
            embed.add_field(name="กำลังเล่นอยู่", value=f"**{self.current['title']}**", inline=False)
            embed.add_field(name="สถานะ Loop", value="🔄 เปิดอยู่" if self.is_looping else "❌ ปิดอยู่", inline=True)
            embed.add_field(name="คิวที่เหลือ", value=f"{self.queue.qsize()} เพลง", inline=True)
            
            view = MusicControlView(self)

            if self.panel_message:
                try:
                    await self.panel_message.edit(embed=embed, view=view)
                except discord.NotFound:
                    self.panel_message = await self.channel.send(embed=embed, view=view)
            else:
                self.panel_message = await self.channel.send(embed=embed, view=view)
        finally:
            self._updating = False

    def destroy(self, guild):
        return self.bot.loop.create_task(guild.voice_client.disconnect())

class MusicControlView(discord.ui.View):
    def __init__(self, player):
        super().__init__(timeout=None)
        self.player = player

    @discord.ui.button(label="Pause/Resume", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("หยุดเล่นชั่วคราวแล้ว!", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("เล่นเพลงต่อแล้ว!", ephemeral=True)
        else:
            await interaction.response.send_message("ไม่มีเพลงที่กำลังเล่นอยู่", ephemeral=True)

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            self.player.is_looping = False
            vc.stop()
            await interaction.response.send_message("ข้ามเพลงเรียบร้อย!", ephemeral=True)
        else:
            await interaction.response.send_message("ไม่มีเพลงให้ข้าม", ephemeral=True)

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.success, emoji="🔁")
    async def toggle_loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.is_looping = not self.player.is_looping
        status = "เปิด" if self.player.is_looping else "ปิด"
        await self.player.update_panel()
        await interaction.response.send_message(f"{status} การเล่นซ้ำ (Loop) แล้ว!", ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = interaction.guild.voice_client
        if vc:
            self.player.queue = asyncio.Queue()
            self.player.is_looping = False
            vc.stop()
            await vc.disconnect()
            await interaction.response.send_message("หยุดเล่นเพลงและออกจากห้องแล้ว!", ephemeral=True)

players = {}

def get_player(interaction):
    try:
        player = players[interaction.guild.id]
    except KeyError:
        player = MusicPlayer(interaction)
        players[interaction.guild.id] = player
    return player

# ==========================================
# 5. Dynamic Verification Modal & View
# ==========================================
class DynamicVerifyModal(discord.ui.Modal, title="ยืนยันตัวตน"):
    def __init__(self):
        super().__init__()
        self.inputs = []
        for q in config["verify_questions"]:
            text_input = discord.ui.TextInput(
                label=q["label"][:45],
                placeholder=q.get("placeholder", "")[:100],
                required=q.get("required", True),
                max_length=150
            )
            self.inputs.append(text_input)
            self.add_item(text_input)

    async def on_submit(self, interaction: discord.Interaction):
        role_id = config["verify_role_id"]
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                await interaction.user.add_roles(role)

        log_ch_id = config["verify_log_channel_id"]
        if log_ch_id:
            log_channel = interaction.guild.get_channel(log_ch_id)
            if log_channel:
                embed = discord.Embed(
                    description=f"{interaction.user.mention} ☑️ **ได้รับยศเรียบร้อยแล้ว**",
                    color=discord.Color.from_rgb(47, 49, 54)
                )
                embed.set_thumbnail(url=interaction.user.display_avatar.url)
                
                for item in self.inputs:
                    val = item.value.strip() if item.value else "ไม่ระบุ"
                    embed.add_field(
                        name=f"ℹ️ {item.label}:",
                        value=f"└ {val}",
                        inline=False
                    )
                embed.set_footer(text=f"ID: {interaction.user.id}")
                await log_channel.send(content=f"{interaction.user.mention}", embed=embed)

        await interaction.response.send_message("ยืนยันตัวตนเรียบร้อยแล้วครับ!", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ยืนยันตัวตน", style=discord.ButtonStyle.success, emoji="📌", custom_id="dynamic_verify_btn_v3")
    async def verify_button_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(DynamicVerifyModal())

# ==========================================
# 6. Master Control Panel
# ==========================================
class EditVerifyQuestionsModal(discord.ui.Modal, title="ตั้งค่าคำถามยืนยันตัวตน (สูงสุด 5 ข้อ)"):
    q1 = discord.ui.TextInput(label="คำถามข้อที่ 1", default=config["verify_questions"][0]["label"] if len(config["verify_questions"]) > 0 else "", required=True)
    q2 = discord.ui.TextInput(label="คำถามข้อที่ 2 (เว้นว่างได้)", default=config["verify_questions"][1]["label"] if len(config["verify_questions"]) > 1 else "", required=False)
    q3 = discord.ui.TextInput(label="คำถามข้อที่ 3 (เว้นว่างได้)", default=config["verify_questions"][2]["label"] if len(config["verify_questions"]) > 2 else "", required=False)
    q4 = discord.ui.TextInput(label="คำถามข้อที่ 4 (เว้นว่างได้)", default=config["verify_questions"][3]["label"] if len(config["verify_questions"]) > 3 else "", required=False)
    q5 = discord.ui.TextInput(label="คำถามข้อที่ 5 (เว้นว่างได้)", default=config["verify_questions"][4]["label"] if len(config["verify_questions"]) > 4 else "", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        new_questions = []
        raw_inputs = [self.q1.value, self.q2.value, self.q3.value, self.q4.value, self.q5.value]
        
        for text in raw_inputs:
            if text and text.strip():
                new_questions.append({
                    "label": text.strip(),
                    "placeholder": "กรอกข้อมูล...",
                    "required": True
                })
        
        if not new_questions:
            await interaction.response.send_message("ต้องมีคำถามอย่างน้อย 1 ข้อครับ!", ephemeral=True)
            return

        config["verify_questions"] = new_questions
        await interaction.response.send_message(f"อัปเดตคำถามยืนยันตัวตนจำนวน {len(new_questions)} ข้อเรียบร้อยแล้ว!", ephemeral=True)

class EditVerifyEmbedModal(discord.ui.Modal, title="แต่งข้อความ Embed ยืนยันตัวตน"):
    title_input = discord.ui.TextInput(label="หัวข้อ (Title)", default=config["verify_title"], required=True)
    desc_input = discord.ui.TextInput(label="รายละเอียด (Description)", default=config["verify_desc"], style=discord.TextStyle.paragraph, required=True)

    async def on_submit(self, interaction: discord.Interaction):
        config["verify_title"] = self.title_input.value
        config["verify_desc"] = self.desc_input.value
        await interaction.response.send_message("อัปเดตข้อความ Embed สำเร็จ!", ephemeral=True)

class EditWelcomeModal(discord.ui.Modal, title="ตั้งค่าข้อความ คนเข้า/ออก"):
    welcome_msg = discord.ui.TextInput(label="ข้อความคนเข้า (ใช้ {member} แทนชื่อ)", default=config["welcome_message"], style=discord.TextStyle.paragraph)
    goodbye_msg = discord.ui.TextInput(label="ข้อความคนออก (ใช้ {member} แทนชื่อ)", default=config["goodbye_message"], style=discord.TextStyle.paragraph)
    img_url = discord.ui.TextInput(label="URL รูปภาพวอลเปเปอร์", default=config["welcome_image_url"], required=False)

    async def on_submit(self, interaction: discord.Interaction):
        config["welcome_message"] = self.welcome_msg.value
        config["goodbye_message"] = self.goodbye_msg.value
        config["welcome_image_url"] = self.img_url.value
        await interaction.response.send_message("อัปเดตข้อความแจ้งเตือนสำเร็จ!", ephemeral=True)

class MasterControlPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="เลือกยศที่จะให้เมื่อยืนยันตัวตน...",
        cls=discord.ui.RoleSelect,
        custom_id="select_verify_role"
    )
    async def select_role(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        config["verify_role_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่ายศยืนยันตัวตนเป็น **{select.values[0].name}** เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องส่ง Log ยืนยันตัวตน...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_log_channel"
    )
    async def select_log_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["verify_log_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องส่ง Log เป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องแจ้งเตือน คนเข้า...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_welcome_channel"
    )
    async def select_welcome_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["welcome_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องแจ้งคนเข้าเป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.select(
        placeholder="เลือกห้องแจ้งเตือน คนออก...",
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        custom_id="select_goodbye_channel"
    )
    async def select_goodbye_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        config["goodbye_channel_id"] = select.values[0].id
        await interaction.response.send_message(f"ตั้งค่าห้องแจ้งคนออกเป็น {select.values[0].mention} เรียบร้อย!", ephemeral=True)

    @discord.ui.button(label="📝 แก้ไข/เพิ่มคำถาม", style=discord.ButtonStyle.secondary, emoji="❓", row=4)
    async def edit_questions(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditVerifyQuestionsModal())

    @discord.ui.button(label="✏️ แต่ง Embed ยืนยัน", style=discord.ButtonStyle.primary, emoji="🎨", row=4)
    async def edit_verify_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditVerifyEmbedModal())

    @discord.ui.button(label="🖼️ แต่งข้อความคนเข้า/ออก", style=discord.ButtonStyle.primary, emoji="👋", row=4)
    async def edit_welcome_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(EditWelcomeModal())

    @discord.ui.button(label="🚀 ส่งปุ่มยืนยันตัวตน", style=discord.ButtonStyle.success, emoji="✅", row=4)
    async def spawn_verify_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title=config["verify_title"],
            description=config["verify_desc"],
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed, view=VerifyView())
        await interaction.response.send_message("ส่งปุ่มยืนยันตัวตนเรียบร้อยแล้ว!", ephemeral=True)

# ==========================================
# 7. Slash Commands (คำสั่งบอท)
# ==========================================
@bot.tree.command(name="setup_panel", description="เปิดแผงควบคุมตั้งค่าระบบบอท (Control Panel)")
async def slash_setup_panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="⚙️ Master Control Panel - แผงควบคุมบอท",
        description="เลือกปรับแต่งคำถาม ยศ และห้องบริการต่าง ๆ ผ่านเมนูด้านล่างได้ทันทีครับ",
        color=discord.Color.purple()
    )
    await interaction.response.send_message(embed=embed, view=MasterControlPanel(), ephemeral=True)

def get_spotify_tracks(query):
    if not sp:
        return []
    clean_url = query.split('?')[0]
    queries = []
    try:
        if "/track/" in clean_url:
            t = sp.track(clean_url)
            queries.append(f"{t.get('name', '')} {t['artists'][0]['name'] if t.get('artists') else ''}")
        elif "/playlist/" in clean_url:
            res = sp.playlist_items(clean_url, limit=10)
            for item in res.get('items', []):
                t = item.get('track')
                if t:
                    queries.append(f"{t.get('name', '')} {t['artists'][0]['name'] if t.get('artists') else ''}")
        elif "/album/" in clean_url:
            res = sp.album_tracks(clean_url, limit=10)
            for t in res.get('items', []):
                if t:
                    queries.append(f"{t.get('name', '')} {t['artists'][0]['name'] if t.get('artists') else ''}")
    except Exception as e:
        print(f"Spotify Error: {e}")
    return queries

def extract_yt_info(search_term):
    yt_query = search_term if search_term.startswith("http") else f"ytsearch1:{search_term}"
    try:
        return ytdl.extract_info(yt_query, download=False)
    except Exception as e:
        print(f"yt-dlp Error: {e}")
        return None

@bot.tree.command(name="play", description="เปิดเพลงจาก YouTube หรือ Spotify")
@app_commands.describe(search="ชื่อเพลง, ลิงก์ YouTube หรือ ลิงก์ Spotify")
async def slash_play(interaction: discord.Interaction, search: str):
    await interaction.response.defer()

    if not interaction.user.voice:
        await interaction.followup.send("ดีนต้องเข้าห้องเสียงก่อนสั่งเปิดเพลงนะ!", ephemeral=True)
        return

    if interaction.guild.voice_client is None:
        try:
            await interaction.user.voice.channel.connect()
        except discord.errors.ClientException:
            pass
        except Exception as e:
            await interaction.followup.send(f"ไม่สามารถเข้าห้องเสียงได้: {e}")
            return

    player = get_player(interaction)
    query = search.strip()
    search_queries = []

    if "open.spotify.com" in query:
        if not sp:
            await interaction.followup.send("ยังไม่ได้ตั้งค่า SPOTIPY_CLIENT_ID และ SPOTIPY_CLIENT_SECRET ครับ!")
            return
        
        try:
            search_queries = await asyncio.wait_for(
                bot.loop.run_in_executor(None, get_spotify_tracks, query),
                timeout=10.0
            )
        except asyncio.TimeoutError:
            await interaction.followup.send("ดึงข้อมูล Spotify นานเกินไป กรุณาลองใหม่อีกครั้งครับ")
            return
    else:
        search_queries.append(query)

    if not search_queries:
        await interaction.followup.send("ไม่พบรายการเพลงที่ต้องการค้นหาครับ!")
        return

    added_count = 0
    first_title = ""

    for item_query in search_queries:
        try:
            info = await asyncio.wait_for(
                bot.loop.run_in_executor(None, extract_yt_info, item_query),
                timeout=15.0
            )
        except asyncio.TimeoutError:
            continue

        if not info:
            continue

        video_data = None
        if 'entries' in info and info['entries']:
            valid = [e for e in info['entries'] if e]
            if valid:
                video_data = valid[0]
        else:
            video_data = info

        if video_data:
            stream_url = video_data.get('url') or f"https://www.youtube.com/watch?v={video_data.get('id')}"
            track_title = video_data.get('title', 'Unknown Title')
            
            await player.queue.put({'url': stream_url, 'title': track_title})
            added_count += 1
            if not first_title:
                first_title = track_title

    if added_count == 1:
        await interaction.followup.send(f"เพิ่มเพลง **{first_title}** เข้าคิวเรียบร้อยครับ!")
    elif added_count > 1:
        await interaction.followup.send(f"เพิ่มเพลงเข้าคิวทั้งหมด **{added_count}** เพลงเรียบร้อยครับ!")
    else:
        await interaction.followup.send("ไม่สามารถดึงข้อมูลเพลงจาก YouTube ได้ครับ")

    if player.current:
        await player.update_panel()

@bot.tree.command(name="stop", description="หยุดเล่นเพลงและให้ออกจากห้องเสียง")
async def slash_stop(interaction: discord.Interaction):
    if interaction.guild.id in players:
        del players[interaction.guild.id]
    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("หยุดเล่นเพลงและออกจากห้องเรียบร้อยครับ!")
    else:
        await interaction.response.send_message("บอทไม่ได้อยู่ในห้องเสียงครับ", ephemeral=True)

# ==========================================
# 8. Event Listeners
# ==========================================
@bot.event
async def on_member_join(member):
    ch_id = config["welcome_channel_id"]
    if ch_id:
        channel = member.guild.get_channel(ch_id)
        if channel:
            text = config["welcome_message"].format(member=member.mention)
            embed = discord.Embed(
                title="👋 ยินดีต้อนรับสมาชิกใหม่!", 
                description=text, 
                color=discord.Color.green()
            )
            avatar_url = member.display_avatar.url
            embed.set_thumbnail(url=avatar_url)
            
            if config["welcome_image_url"]:
                embed.set_image(url=config["welcome_image_url"])
            else:
                embed.set_image(url=avatar_url)

            await channel.send(content=f"ยินดีต้อนรับ {member.mention} !", embed=embed)

@bot.event
async def on_member_remove(member):
    ch_id = config["goodbye_channel_id"]
    if ch_id:
        channel = member.guild.get_channel(ch_id)
        if channel:
            text = config["goodbye_message"].format(member=member.display_name)
            embed = discord.Embed(
                title="😢 สมาชิกออกจากเซิร์ฟเวอร์", 
                description=text, 
                color=discord.Color.red()
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            await channel.send(embed=embed)

@bot.event
async def on_ready():
    print(f"ล็อกอินเรียบร้อย! บอท {bot.user.name} พร้อมใช้งานแล้ว!")
    bot.add_view(VerifyView())

# ==========================================
# 9. Main Startup Execution
# ==========================================
async def main():
    await start_dummy_web_server()
    await bot.start(DISCORD_BOT_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
