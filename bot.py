import discord
from discord import Option
from discord.ext import commands
import yt_dlp
import asyncio
import random
import os
import tempfile
import re

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

queues = {}       # {guild_id: [song1, song2,...]}
loop_mode = {}    # {guild_id: "none"/"song"/"queue"}

SHORTS_REGEX = re.compile(r'(https?://)?(www\.)?youtube\.com/shorts/(\w+)')

# ---------- Helpers ----------
def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_loop_mode(guild_id):
    return loop_mode.get(guild_id, "none")

def set_loop_mode(guild_id, mode):
    loop_mode[guild_id] = mode

async def fetch_tempfile(query):
    # Detect Shorts
    match = SHORTS_REGEX.match(query)
    if match:
        video_id = match.group(3)
        query = f"https://www.youtube.com/watch?v={video_id}"

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'http_headers': {'User-Agent': 'Mozilla/5.0'},
        # Fallback cho SoundCloud
        'source_address': '0.0.0.0',
    }

    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)

    def run():
        ydl_opts['outtmpl'] = temp_file.name
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                formats = info.get('formats', [])
                audio_formats = [f for f in formats if f['acodec'] != 'none']
                if not audio_formats:
                    raise ValueError("❌ Không có audio hợp lệ")
                ydl.download([info['webpage_url']])
                return info
        except Exception:
            # Fallback SoundCloud
            ydl_opts['default_search'] = 'scsearch'
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                ydl.download([info['webpage_url']])
                return info

    info = await loop.run_in_executor(None, run)
    return temp_file.name, info['title'], info['webpage_url']

# ---------- Play loop ----------
async def play_loop(vc, guild_id, channel):
    queue = get_queue(guild_id)
    while queue:
        song = queue[0]
        file_path, title, link = song

        done = asyncio.Event()

        def after_play(error):
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
            vc.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegOpusAudio(file_path)
        vc.play(source, after=after_play)
        embed = discord.Embed(title="🎵 Đang phát", description=f"[{title}]({link})", color=0x1DB954)
        asyncio.create_task(channel.send(embed=embed))

        await done.wait()

        mode = get_loop_mode(guild_id)
        if mode == "song":
            continue
        elif mode == "queue":
            queue.append(queue.pop(0))
        else:
            queue.pop(0)

# ---------- Commands ----------
@bot.slash_command(name="play", description="Phát nhạc / playlist")
async def play(ctx, *, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    queue = get_queue(ctx.guild.id)
    try:
        file_path, title, link = await fetch_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi lấy nhạc: {e}")

    queue.append((file_path, title, link))
    embed = discord.Embed(title="➕ Đã thêm vào queue", description=f"[{title}]({link})", color=0x1DB954)
    await ctx.followup.send(embed=embed)

    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))

@bot.slash_command(name="queue", description="Xem danh sách queue")
async def queue_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue:
        return await ctx.respond("📭 Queue trống")
    embed = discord.Embed(title="📜 Queue", color=0x1DB954)
    for i, (_, title, link) in enumerate(queue, start=1):
        embed.add_field(name=f"{i}. {title}", value=link, inline=False)
    await ctx.respond(embed=embed)

@bot.slash_command(name="shuffle", description="Xáo trộn queue")
async def shuffle_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue:
        return await ctx.respond("❌ Queue trống")
    random.shuffle(queue)
    await ctx.respond("🔀 Queue đã được shuffle")

@bot.slash_command(name="loop", description="Loop bài hiện tại hoặc queue")
async def loop_cmd(ctx, mode: Option(str, "none/song/queue")):
    mode = mode.lower()
    if mode not in ["none", "song", "queue"]:
        return await ctx.respond("❌ Chọn: none / song / queue")
    set_loop_mode(ctx.guild.id, mode)
    await ctx.respond(f"🔁 Loop mode: {mode}")

@bot.slash_command(name="skip", description="Bỏ qua bài đang phát")
async def skip(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    if vc and vc.is_playing():
        vc.stop()
        await ctx.respond("⏭️ Bài đã skip")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="stop", description="Dừng nhạc và xoá queue")
async def stop(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    for f, _, _ in queue:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass
    queue.clear()
    if vc:
        vc.stop()
    await ctx.respond("⏹️ Đã dừng nhạc và xoá queue")

# ---------- Join/Leave ----------
@bot.slash_command(name="join", description="Vào voice channel")
async def join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    vc = ctx.guild.voice_client
    if vc:
        await vc.move_to(ctx.author.voice.channel)
    else:
        vc = await ctx.author.voice.channel.connect()
    await ctx.respond(f"✅ Bot đã vào {ctx.author.voice.channel.name}")

@bot.slash_command(name="leave", description="Rời voice")
async def leave(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    for f, _, _ in queue:
        try:
            if os.path.exists(f):
                os.remove(f)
        except:
            pass
    queue.clear()
    if vc:
        vc.stop()
        await vc.disconnect()
    await ctx.respond("👋 Bot đã rời voice và xoá queue")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(os.getenv("TOKEN"))
