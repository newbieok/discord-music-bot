import discord
from discord import Option
import yt_dlp
import asyncio
import random
import os

# ---------- Bot setup ----------
intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ---------- Global state ----------
queues = {}      # {guild_id: [(url, title, link, thumbnail), ...]}
loop_mode = {}   # {guild_id: "none"/"song"/"queue"}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_loop_mode(guild_id):
    return loop_mode.get(guild_id, "none")

def set_loop_mode(guild_id, mode):
    loop_mode[guild_id] = mode

# ---------- YouTube fetch (bền hơn) ----------
async def fetch_youtube(query):
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'cookiefile': 'cookies.txt',
        'http_headers': {'User-Agent': 'Mozilla/5.0'}
    }

    loop = asyncio.get_event_loop()

    def run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info:
                    info = info['entries'][0]
                return info
            except yt_dlp.utils.DownloadError as e:
                raise RuntimeError(f"yt_dlp error: {e}")

    info = await loop.run_in_executor(None, run)

    return (
        info['url'],           # streamable URL
        info['title'],
        info['webpage_url'],
        info.get('thumbnail')
    )

# ---------- Play loop ----------
async def play_loop(vc, guild_id, channel):
    ffmpeg_path = "ffmpeg"
    queue = get_queue(guild_id)

    while queue:
        url, title, link, thumbnail = queue[0]

        try:
            source = await discord.FFmpegOpusAudio.from_probe(
                url,
                executable=ffmpeg_path,
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn'
            )
        except Exception as e:
            await channel.send(f"❌ Lỗi phát: {e}")
            queue.pop(0)
            continue

        done = asyncio.Event()

        def after_playing(error):
            vc.loop.call_soon_threadsafe(done.set)

        vc.play(source, after=after_playing)

        await channel.send(f"🎶 Đang phát: [{title}]({link})")

        await done.wait()

        # Xử lý loop mode
        mode = get_loop_mode(guild_id)
        if mode == "song":
            continue
        elif mode == "queue":
            queue.append(queue.pop(0))
        else:
            queue.pop(0)

# ---------- Slash command /play ----------
@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)

    await ctx.defer()  # báo Discord bot đang xử lý

    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    queue = get_queue(ctx.guild.id)

    try:
        url, title, link, thumbnail = await fetch_youtube(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi lấy nhạc: {e}")

    # Chặn trùng
    if any(link == t[2] for t in queue):
        return await ctx.followup.send("⚠️ Bài này đã có trong queue")

    queue.append((url, title, link, thumbnail))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue")

    # Nếu chưa phát thì start loop
    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))

# ---------- Các command cơ bản khác ----------
@bot.slash_command(name="join", description="Vào voice channel")
async def join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc:
        await vc.move_to(ctx.author.voice.channel)
    else:
        vc = await ctx.author.voice.channel.connect()
    await ctx.followup.send(f"✅ Bot đã vào {ctx.author.voice.channel.name}")

@bot.slash_command(name="leave", description="Rời voice")
async def leave(ctx):
    await ctx.defer()
    vc = ctx.guild.voice_client
    if vc:
        await vc.disconnect()
        await ctx.followup.send("👋 Đã rời voice")
    else:
        await ctx.followup.send("❌ Bot chưa vào voice")

@bot.slash_command(name="skip", description="Bỏ qua bài")
async def skip(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    if vc and vc.is_playing():
        vc.stop()
        if queue:
            queue.pop(0)
        await ctx.respond("⏭️ Đã skip")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

# ---------- Run bot ----------
bot.run(os.getenv("TOKEN"))
