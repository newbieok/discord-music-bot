import discord
from discord import Option
import yt_dlp
import asyncio
import random
import os
import tempfile

# ---------- Bot setup ----------
intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ---------- Global state ----------
queues = {}      # {guild_id: [(file_path, title, link), ...]}
loop_mode = {}   # {guild_id: "none"/"song"/"queue"}

def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = []
    return queues[guild_id]

def get_loop_mode(guild_id):
    return loop_mode.get(guild_id, "none")

def set_loop_mode(guild_id, mode):
    loop_mode[guild_id] = mode

# ---------- Fetch and download audio temporarily ----------
async def fetch_youtube_tempfile(query):
    ydl_opts = {
        'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'cookiefile': 'cookies.txt',
        'http_headers': {'User-Agent': 'Mozilla/5.0'},
    }

    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)

    def run():
        ydl_opts['outtmpl'] = temp_file.name
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=True)
            if 'entries' in info:
                info = info['entries'][0]
            return info

    info = await loop.run_in_executor(None, run)
    return temp_file.name, info['title'], info['webpage_url']

# ---------- Play loop ----------
async def play_loop(vc, guild_id, channel):
    queue = get_queue(guild_id)

    while queue:
        file_path, title, link = queue[0]

        done = asyncio.Event()

        def after_playing(error):
            # xóa file tạm sau khi phát
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
            vc.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegOpusAudio(file_path)
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
        file_path, title, link = await fetch_youtube_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi lấy nhạc: {e}")

    # Chặn trùng
    if any(link == t[2] for t in queue):
        # xóa file tạm nếu trùng
        if os.path.exists(file_path):
            os.remove(file_path)
        return await ctx.followup.send("⚠️ Bài này đã có trong queue")

    queue.append((file_path, title, link))
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
        vc.stop()
        # xóa tất cả file tạm còn trong queue
        queue = get_queue(ctx.guild.id)
        for f, _, _ in queue:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass
        queue.clear()
        await vc.disconnect()
        await ctx.followup.send("👋 Đã rời voice và xoá queue")
    else:
        await ctx.followup.send("❌ Bot chưa vào voice")

@bot.slash_command(name="skip", description="Bỏ qua bài")
async def skip(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    if vc and vc.is_playing():
        vc.stop()
        if queue:
            # xóa file tạm của bài đang skip
            f, _, _ = queue.pop(0)
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass
        await ctx.respond("⏭️ Đã skip")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

# ---------- Run bot ----------
bot.run(os.getenv("TOKEN"))
