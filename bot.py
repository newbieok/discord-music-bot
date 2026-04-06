import discord
from discord import Option
from discord.ext import commands
import yt_dlp
import asyncio
import tempfile
import os
import re
from datetime import timedelta
import random

# ---------- Bot setup ----------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

# ---------- Globals ----------
queues = {}        # guild_id: [(file_path, title, link, duration)]
loop_mode = {}     # guild_id: "none"/"song"/"queue"
update_embeds = {} # guild_id: embed message
SHORTS_REGEX = re.compile(r'(https?://)?(www\.)?youtube\.com/shorts/(\w+)')

# ---------- Helper functions ----------
def get_queue(gid): return queues.setdefault(gid, [])
def get_loop_mode(gid): return loop_mode.get(gid, "none")
def set_loop_mode(gid, mode): loop_mode[gid] = mode
def format_duration(sec): return str(timedelta(seconds=int(sec)))

# ---------- Fetch audio ----------
async def fetch_tempfile(query):
    match = SHORTS_REGEX.match(query)
    if match:
        query = f"https://www.youtube.com/watch?v={match.group(3)}"

    ydl_opts = {
        'format': 'bestaudio/best',
        'noplaylist': True,
        'quiet': True,
        'default_search': 'ytsearch',
        'http_headers': {'User-Agent':'Mozilla/5.0'},
    }

    loop = asyncio.get_event_loop()
    temp_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)

    def run():
        ydl_opts['outtmpl'] = temp_file.name
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info: info = info['entries'][0]
                audio = [f for f in info.get('formats',[]) if f['acodec'] != 'none']
                if not audio: raise ValueError("❌ Không có audio hợp lệ")
                ydl.download([info['webpage_url']])
                return info
        except Exception:
            ydl_opts['default_search'] = 'scsearch'
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info: info = info['entries'][0]
                ydl.download([info['webpage_url']])
                return info

    info = await loop.run_in_executor(None, run)
    return temp_file.name, info['title'], info['webpage_url'], info.get('duration', 0)

# ---------- Embed helper ----------
def make_now_playing_embed(title, link, elapsed, duration, mode):
    color = 0x1DB954
    embed = discord.Embed(title="🎵 Now Playing", description=f"[{title}]({link})", color=color)
    loop_emoji = {"none":"❌","song":"🔂","queue":"🔁"}.get(mode,"❌")
    embed.set_footer(text=f"Loop: {loop_emoji}")

    bar_len = 25
    if duration == 0:
        bar = "🔴 LIVE STREAM"
        embed.add_field(name="⏱", value="LIVE STREAM", inline=True)
        embed.add_field(name="Progress", value=bar, inline=False)
    else:
        if elapsed > duration: elapsed = duration
        filled = int(bar_len * elapsed / max(duration,1))
        bar = "▬"*filled + "🔘" + "▬"*(bar_len-filled)
        embed.add_field(name="⏱", value=f"{format_duration(elapsed)} / {format_duration(duration)}", inline=True)
        embed.add_field(name="Progress", value=bar, inline=False)
    return embed

# ---------- Progress updater ----------
async def update_progress_embed(gid, duration, start):
    msg = update_embeds.get(gid)
    if not msg: return
    while True:
        vc = msg.guild.voice_client
        if not vc or not vc.is_playing(): break
        elapsed = int(asyncio.get_event_loop().time() - start)
        mode = get_loop_mode(msg.guild.id)
        embed = make_now_playing_embed(msg.embeds[0].description[1:], msg.embeds[0].description, elapsed, duration, mode)
        try: await msg.edit(embed=embed)
        except: break
        await asyncio.sleep(1)

# ---------- Play loop ----------
async def play_loop(vc, gid, channel):
    queue = get_queue(gid)
    while queue:
        file_path, title, link, duration = queue[0]
        done = asyncio.Event()

        def after_play(error):
            try: os.remove(file_path)
            except: pass
            if error:
                print(f"⚠️ Error playing {title}: {error}")
            vc.loop.call_soon_threadsafe(done.set)

        try:
            source = discord.FFmpegOpusAudio(
                file_path,
                before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                options='-vn -loglevel panic'
            )
        except Exception as e:
            print(f"⚠️ FFmpeg error: {e}")
            queue.pop(0)
            continue

        vc.play(source, after=after_play)

        start = asyncio.get_event_loop().time()
        mode = get_loop_mode(gid)
        msg = await channel.send(embed=make_now_playing_embed(title, link, 0, duration, mode))
        update_embeds[gid] = msg
        progress_task = asyncio.create_task(update_progress_embed(gid, duration, start))

        await done.wait()
        progress_task.cancel()

        mode = get_loop_mode(gid)
        if mode == "song":
            continue
        elif mode == "queue":
            queue.append(queue.pop(0))
        else:
            queue.pop(0)
        update_embeds.pop(gid, None)

# ---------- Slash commands ----------

@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, *, query: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc:
        vc = await ctx.author.voice.channel.connect()
    try:
        file_path, title, link, duration = await fetch_tempfile(query)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi lấy nhạc: {e}")

    queue = get_queue(ctx.guild.id)
    queue.append((file_path, title, link, duration))

    embed = discord.Embed(title="➕ Đã thêm vào queue", description=f"[{title}]({link})", color=0x1DB954)
    embed.add_field(name="Duration", value=format_duration(duration))
    await ctx.followup.send(embed=embed)

    if not hasattr(bot, "play_tasks"):
        bot.play_tasks = {}
    if ctx.guild.id not in bot.play_tasks or bot.play_tasks[ctx.guild.id].done():
        bot.play_tasks[ctx.guild.id] = asyncio.create_task(play_loop(vc, ctx.guild.id, ctx.channel))

@bot.slash_command(name="queue", description="Xem queue")
async def queue_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue: return await ctx.respond("📭 Queue trống")
    embed = discord.Embed(title="📜 Queue", color=0x1DB954)
    for i, (_, title, link, duration) in enumerate(queue, 1):
        embed.add_field(name=f"{i}. {title}", value=f"{link} | ⏱ {format_duration(duration)}", inline=False)
    await ctx.respond(embed=embed)

@bot.slash_command(name="skip", description="Bỏ qua bài")
async def skip(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await ctx.respond("⏭️ Bài đã skip")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="stop", description="Dừng nhạc và xoá queue")
async def stop(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    for f, _, _, _ in queue:
        try: os.remove(f)
        except: pass
    queue.clear()
    if vc: vc.stop()
    await ctx.respond("⏹️ Đã dừng nhạc và xoá queue")

@bot.slash_command(name="join", description="Vào voice channel")
async def join(ctx):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Bạn phải vào voice trước", ephemeral=True)
    vc = ctx.guild.voice_client
    if vc: await vc.move_to(ctx.author.voice.channel)
    else: vc = await ctx.author.voice.channel.connect()
    await ctx.respond(f"✅ Bot đã vào {ctx.author.voice.channel.name}")

@bot.slash_command(name="leave", description="Rời voice")
async def leave(ctx):
    vc = ctx.guild.voice_client
    queue = get_queue(ctx.guild.id)
    for f, _, _, _ in queue:
        try: os.remove(f)
        except: pass
    queue.clear()
    if vc:
        vc.stop()
        await vc.disconnect()
    await ctx.respond("👋 Bot đã rời voice và xoá queue")

@bot.slash_command(name="pause", description="Tạm dừng")
async def pause(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.respond("⏸️ Đã pause")
    else:
        await ctx.respond("❌ Không có bài đang phát")

@bot.slash_command(name="resume", description="Tiếp tục")
async def resume(ctx):
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.respond("▶️ Đã resume")
    else:
        await ctx.respond("❌ Không có bài đang pause")

@bot.slash_command(name="shuffle", description="Xáo trộn queue")
async def shuffle_cmd(ctx):
    queue = get_queue(ctx.guild.id)
    if not queue: return await ctx.respond("❌ Queue trống")
    random.shuffle(queue)
    await ctx.respond("🔀 Queue đã được shuffle")

@bot.slash_command(name="loop", description="Loop song / queue / none")
async def loop_cmd(ctx, mode: Option(str, "none/song/queue")):
    mode = mode.lower()
    if mode not in ["none","song","queue"]: return await ctx.respond("❌ Chọn: none / song / queue")
    set_loop_mode(ctx.guild.id, mode)
    await ctx.respond(f"🔁 Loop mode: {mode}")

@bot.slash_command(name="ping", description="Test bot")
async def ping(ctx):
    await ctx.respond("🏓 Pong")

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

# ---------- Run bot ----------
bot.run(os.getenv("TOKEN"))
