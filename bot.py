import discord
from discord.ui import View
from discord import ButtonStyle
import yt_dlp
import asyncio
import tempfile, shutil, os
from datetime import timedelta
import random

intents = discord.Intents.all()
bot = discord.Bot(intents=intents)

# ---------- GLOBAL STATE ----------
queues = {}        # {guild_id: [(file_path,title,link,duration)]}
loop_mode = {}     # {guild_id: none/song/queue}
update_embeds = {} # {guild_id: message embed}
temp_dirs = {}     # {guild_id: temp folder path}

MAX_QUEUE = 30     # giới hạn queue

# ---------- HELPERS ----------
def get_queue(gid): return queues.setdefault(gid, [])
def get_loop_mode(gid): return loop_mode.get(gid,"none")
def set_loop_mode(gid,mode): loop_mode[gid]=mode
def format_duration(sec): return str(timedelta(seconds=int(sec)))

def make_embed(title, link, elapsed, duration, mode):
    embed = discord.Embed(title="🎵 Now Playing", description=f"[{title}]({link})", color=0x1DB954)
    loop_emoji = {"none":"❌","song":"🔂","queue":"🔁"}.get(mode,"❌")
    embed.set_footer(text=f"Loop: {loop_emoji}")
    bar_len = 25
    if duration==0:
        embed.add_field(name="⏱", value="LIVE STREAM", inline=True)
        embed.add_field(name="Progress", value="🔴 LIVE", inline=False)
    else:
        elapsed = min(elapsed, duration)
        filled = int(bar_len*elapsed/max(duration,1))
        bar = "▬"*filled + "🔘" + "▬"*(bar_len-filled)
        embed.add_field(name="⏱", value=f"{format_duration(elapsed)}/{format_duration(duration)}", inline=True)
        embed.add_field(name="Progress", value=bar, inline=False)
    return embed

# ---------- Music Control Buttons ----------
class MusicControlView(View):
    def __init__(self,gid):
        super().__init__(timeout=None)
        self.gid = gid

    @discord.ui.button(label="⏭️ Skip", style=ButtonStyle.green)
    async def skip(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.stop()
        await interaction.response.send_message("⏭️ Skip", ephemeral=True)

    @discord.ui.button(label="⏸️ Pause", style=ButtonStyle.blurple)
    async def pause(self, button, interaction):
        vc = interaction.guild.voice_client
        if vc and vc.is_playing(): vc.pause()
        await interaction.response.send_message("⏸️ Pause", ephemeral=True)

    @discord.ui.button(label="▶️ Resume", style=ButtonStyle.green)
    async def resume(self, button, interaction):
        vc = interaction.guild.voice_client
        queue = get_queue(interaction.guild.id)
        if vc and vc.is_paused():
            vc.resume()
            # Gợi ý bài tiếp theo
            next_song = queue[1] if len(queue)>1 else None
            current = queue[0] if queue else None
            if current:
                embed = discord.Embed(title="▶️ Resume", description=f"Đang tiếp tục: [{current[1]}]({current[2]})", color=0x1DB954)
                if next_song:
                    embed.add_field(name="Next Up", value=f"[{next_song[1]}]({next_song[2]})")
                else:
                    embed.add_field(name="Next Up", value="Không còn bài tiếp theo")
            else:
                embed = discord.Embed(title="▶️ Resume", description="Không có bài đang phát", color=0x1DB954)
            view = MusicControlView(interaction.guild.id)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message("❌ Không có bài đang pause", ephemeral=True)

    @discord.ui.button(label="🔁 Loop", style=ButtonStyle.gray)
    async def loop(self, button, interaction):
        mode = get_loop_mode(self.gid)
        new_mode = {"none":"song","song":"queue","queue":"none"}[mode]
        set_loop_mode(self.gid,new_mode)
        await interaction.response.send_message(f"🔁 Loop mode: {new_mode}", ephemeral=True)

    @discord.ui.button(label="🔀 Shuffle", style=ButtonStyle.gray)
    async def shuffle(self, button, interaction):
        queue = get_queue(interaction.guild.id)
        if not queue: await interaction.response.send_message("❌ Queue trống", ephemeral=True)
        else:
            random.shuffle(queue)
            await interaction.response.send_message("🔀 Queue đã shuffle", ephemeral=True)

    @discord.ui.button(label="⏹️ Stop", style=ButtonStyle.red)
    async def stop(self, button, interaction):
        queue = get_queue(interaction.guild.id)
        for f,_,_,_ in queue:
            try: os.remove(f)
            except: pass
        queue.clear()
        vc = interaction.guild.voice_client
        if vc: vc.stop()
        await interaction.response.send_message("⏹️ Stop tất cả", ephemeral=True)

# ---------- Fetch Audio ----------
async def fetch_tempfile(query, gid):
    loop = asyncio.get_event_loop()
    temp_dir = temp_dirs.setdefault(gid, tempfile.mkdtemp(dir="/tmp/music"))
    temp_file = tempfile.NamedTemporaryFile(suffix=".opus", dir=temp_dir, delete=False)
    def run():
        ydl_opts = {
            'format':'bestaudio/best',
            'quiet':True,
            'noplaylist':True,
            'outtmpl': temp_file.name + ".%(ext)s",
            'default_search':'ytsearch'
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(query, download=False)
            if 'entries' in info: info = info['entries'][0]
            ydl.download([info['webpage_url']])
            # convert to opus
            file_path = temp_file.name
            os.system(f"ffmpeg -i \"{temp_file.name}.{info['ext']}\" -c:a libopus -b:a 128k -y \"{file_path}\"")
            try: os.remove(f"{temp_file.name}.{info['ext']}")
            except: pass
            return file_path, info
    file_path, info = await loop.run_in_executor(None, run)
    return file_path, info['title'], info['webpage_url'], info.get('duration',0)

# ---------- Progress updater ----------
async def update_progress(gid, duration):
    msg = update_embeds.get(gid)
    if not msg: return
    vc = msg.guild.voice_client
    while vc and vc.is_playing():
        if duration==0:
            embed = make_embed(msg.embeds[0].title,msg.embeds[0].description,0,0,get_loop_mode(msg.guild.id))
        else:
            try: elapsed = int(vc.source._player._position)
            except: elapsed = 0
            embed = make_embed(msg.embeds[0].title,msg.embeds[0].description,elapsed,duration,get_loop_mode(msg.guild.id))
        try: await msg.edit(embed=embed)
        except: break
        await asyncio.sleep(0.5)

# ---------- Play Loop ----------
async def play_loop(vc,gid,channel):
    queue = get_queue(gid)
    while queue:
        file_path,title,link,duration = queue[0]
        done = asyncio.Event()
        def after_play(error):
            try: os.remove(file_path)
            except: pass
            if vc.loop.is_running():
                vc.loop.call_soon_threadsafe(done.set)
        source = discord.FFmpegOpusAudio(file_path)
        vc.play(source, after=after_play)
        view = MusicControlView(gid)
        msg = await channel.send(embed=make_embed(title,link,0,duration,get_loop_mode(gid)),view=view)
        update_embeds[gid] = msg
        task = asyncio.create_task(update_progress(gid,duration))
        await done.wait()
        task.cancel()
        mode = get_loop_mode(gid)
        if mode=="song": continue
        elif mode=="queue": queue.append(queue.pop(0))
        else: queue.pop(0)
        update_embeds.pop(gid,None)

# ---------- COMMANDS ----------
@bot.slash_command(name="play", description="Phát nhạc")
async def play(ctx, *, query:str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.respond("❌ Vào voice trước", ephemeral=True)
    await ctx.defer()
    vc = ctx.guild.voice_client
    if not vc: vc = await ctx.author.voice.channel.connect()
    queue = get_queue(ctx.guild.id)
    if len(queue)>=MAX_QUEUE:
        return await ctx.followup.send(f"❌ Queue max {MAX_QUEUE} bài")
    try:
        file_path,title,link,duration = await fetch_tempfile(query,ctx.guild.id)
    except Exception as e:
        return await ctx.followup.send(f"❌ Lỗi: {e}")
    queue.append((file_path,title,link,duration))
    await ctx.followup.send(f"➕ [{title}]({link}) vào queue | Queue: {len(queue)} bài")
    if not vc.is_playing() and not vc.is_paused():
        asyncio.create_task(play_loop(vc,ctx.guild.id,ctx.channel))
    elif vc.is_paused(): vc.resume()

# Keep your existing commands for pause, skip, stop, queue, loop, join, leave
# ... (giữ nguyên như trước, sử dụng MusicControlView cho embed/buttons)

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user} | Guilds: {len(bot.guilds)}")

bot.run(os.getenv("TOKEN"))
