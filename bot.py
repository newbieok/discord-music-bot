import discord
from discord import Option
import yt_dlp
import asyncio
import random
import os
import tempfile

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

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

# ---------- Fetch + download audio tạm ----------
async def fetch_youtube_tempfile(query):
    ydl_opts = {
        'format': 'bestaudio/best',  # tự chọn audio tốt nhất
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
            if 'entries' in info:  # nếu là search result
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
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
            vc.loop.call_soon_threadsafe(done.set)

        source = discord.FFmpegOpusAudio(file_path)
        vc.play(source, after=after_playing)
        asyncio.create_task(channel.send(f"🎶 Đang phát: [{title}]({link})"))

        await done.wait()

        mode = get_loop_mode(guild_id)
        if mode == "song":
            continue
        elif mode == "queue":
            queue.append(queue.pop(0))
        else:
            queue.pop(0)

# ---------- Slash commands ----------
@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()

    queue = get_queue(ctx.guild.id)
    try:
        file_path, title, link = await fetch_youtube_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi lấy nhạc: {e}")

    if any(link == t[2] for t in queue):
        if os.path.exists(file_path):
            os.remove(file_path)
        return await ctx.followup.send("⚠️ Bài này đã có trong queue")

    queue.append((file_path, title, link))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue")

    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))

@bot.slash_command(name="pause", description="Tạm dừng bài đang phát")
async def pause(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.respond("⏸️ Đã pause")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="resume", description="Tiếp tục bài đang pause")
async def resume(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.respond("▶️ Đã resume")
    else:
        await ctx.respond("❌ Không có bài đang pause")

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
    await ctx.respond("⏹️ Đã dừng tất cả nhạc và xoá queue")

@bot.slash_command(name="skip", description="Bỏ qua bài")
async def skip(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    if vc and vc.is_playing():
        vc.stop()
        if queue:
            f, _, _ = queue.pop(0)
            try:
                if os.path.exists(f):
                    os.remove(f)
            except:
                pass
        await ctx.respond("⏭️ Đã skip")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="queue", description="Xem danh sách nhạc")
async def queue_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue:
        await ctx.respond("📭 Queue trống")
    else:
        msg = "\n".join([f"{i+1}. [{t[1]}]({t[2]})" for i, t in enumerate(queue)])
        await ctx.respond(f"📜 Queue:\n{msg}")

@bot.slash_command(name="shuffle", description="Xáo trộn queue")
async def shuffle_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue:
        await ctx.respond("❌ Queue trống")
    else:
        random.shuffle(queue)
        await ctx.respond("🔀 Queue đã được shuffle")

@bot.slash_command(name="loop", description="Loop bài hiện tại hoặc queue")
async def loop_cmd(ctx, mode: Option(str, "none / song / queue")):
    mode = mode.lower()
    if mode not in ["none", "song", "queue"]:
        return await ctx.respond("❌ Chọn: none / song / queue")
    set_loop_mode(ctx.guild.id, mode)
    await ctx.respond(f"🔁 Loop mode set: {mode}")

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
        await ctx.followup.send("👋 Đã rời voice và xoá queue")
    else:
        await ctx.followup.send("❌ Bot chưa vào voice")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(os.getenv("TOKEN"))
