import discord
from discord.ext import commands
import yt_dlp
import asyncio
from collections import deque
from dotenv import load_dotenv
import os
import sys

# ─────────────────────────────────────────
#  .env 불러오기
# ─────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("❌ .env에 DISCORD_TOKEN이 없어요!")
    exit(1)

# ─────────────────────────────────────────
#  봇 설정
# ─────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None, tree_cls=discord.app_commands.CommandTree)

# ─────────────────────────────────────────
#  yt-dlp 옵션 (고음질)
# ─────────────────────────────────────────
ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -loglevel error'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

# ─────────────────────────────────────────
#  서버별 상태 관리
# ─────────────────────────────────────────
# guild_id → { queue, volume, loop, now_playing }
guild_states = {}

def get_state(guild_id):
    if guild_id not in guild_states:
        guild_states[guild_id] = {
            "queue": deque(),
            "volume": 0.5,
            "loop": False,
            "now_playing": None
        }
    return guild_states[guild_id]

# ─────────────────────────────────────────
#  YTDLSource 클래스
# ─────────────────────────────────────────
class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title', '제목 없음')
        self.url = data.get('webpage_url') or data.get('url', '')
        self.duration = self._format_duration(data.get('duration', 0))
        self.thumbnail = data.get('thumbnail', '')
        self.uploader = data.get('uploader', '알 수 없음')

    @staticmethod
    def _format_duration(seconds):
        if not seconds:
            return '??:??'
        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f'{hours}:{minutes:02d}:{secs:02d}'
        return f'{minutes}:{secs:02d}'

    @classmethod
    async def from_url(cls, url, *, loop=None, volume=0.5):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)
        )

        if data is None:
            raise ValueError("영상 정보를 불러올 수 없어요.")

        if 'entries' in data:
            data = data['entries'][0]

        stream_url = data['url']
        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)
        return cls(source, data=data, volume=volume)

# ─────────────────────────────────────────
#  다음 곡 재생 함수
# ─────────────────────────────────────────
async def play_next(ctx):
    state = get_state(ctx.guild.id)
    vc = ctx.guild.voice_client

    if not vc or not vc.is_connected():
        return

    # 반복 재생: 현재 곡을 다시 큐 앞에 추가
    if state["loop"] and state["now_playing"]:
        state["queue"].appendleft(state["now_playing"])

    if not state["queue"]:
        state["now_playing"] = None
        # 30초 후 자동 퇴장
        await asyncio.sleep(30)
        state2 = get_state(ctx.guild.id)
        if not state2["queue"] and vc.is_connected():
            await vc.disconnect()
            await ctx.send(embed=discord.Embed(
                description="📭 대기열이 비어 자동 퇴장했어요!",
                color=0xED4245
            ))
        return

    song_info = state["queue"].popleft()

    try:
        player = await YTDLSource.from_url(
            song_info["url"], loop=bot.loop, volume=state["volume"]
        )
        state["now_playing"] = song_info

        def after_play(error):
            if error:
                print(f"재생 오류: {error}")
            asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

        vc.play(player, after=after_play)

        embed = make_now_playing_embed(player, song_info["requester"], state)
        await ctx.send(embed=embed)

    except Exception as e:
        await ctx.send(embed=discord.Embed(
            description=f"❌ 재생 오류: {e}",
            color=0xED4245
        ))
        await play_next(ctx)

# ─────────────────────────────────────────
#  임베드 헬퍼
# ─────────────────────────────────────────
def make_now_playing_embed(player, requester, state):
    embed = discord.Embed(
        title=player.title,
        url=player.url,
        color=0x5865F2
    )
    embed.set_author(name="🎵 지금 재생 중")
    if player.thumbnail:
        embed.set_thumbnail(url=player.thumbnail)
    embed.add_field(name="⏱ 길이", value=player.duration, inline=True)
    embed.add_field(name="👤 요청자", value=requester, inline=True)
    embed.add_field(name="🔁 반복", value="켜짐" if state["loop"] else "꺼짐", inline=True)
    embed.add_field(name="🔊 볼륨", value=f"{int(state['volume'] * 100)}%", inline=True)
    embed.add_field(name="📋 대기열", value=f"{len(state['queue'])}곡 대기 중", inline=True)
    embed.set_footer(text="음악봇 🎧 | !도움 으로 명령어 확인")
    return embed

def make_queue_embed(state):
    embed = discord.Embed(title="📋 대기열", color=0x57F287)
    queue_list = list(state["queue"])

    if state["now_playing"]:
        embed.add_field(
            name="▶ 현재 재생",
            value=f"[{state['now_playing']['title']}]({state['now_playing']['url']})",
            inline=False
        )

    if not queue_list:
        embed.add_field(name="대기열", value="비어있어요!", inline=False)
    else:
        desc = "\n".join(
            f"`{i+1}.` [{s['title']}]({s['url']}) — {s['requester']}"
            for i, s in enumerate(queue_list[:10])
        )
        if len(queue_list) > 10:
            desc += f"\n... 외 {len(queue_list) - 10}곡"
        embed.add_field(name=f"총 {len(queue_list)}곡 대기 중", value=desc, inline=False)

    embed.set_footer(text=f"🔁 반복: {'켜짐' if state['loop'] else '꺼짐'} | 🔊 볼륨: {int(state['volume'] * 100)}%")
    return embed

# ─────────────────────────────────────────
#  명령어
# ─────────────────────────────────────────

@bot.command(name="실행", aliases=["play", "p"])
async def play(ctx, *, query: str):
    """유튜브 URL 또는 검색어로 음악 재생"""
    if not ctx.author.voice:
        return await ctx.send(embed=discord.Embed(
            description="❌ 음성 채널에 먼저 들어가!", color=0xED4245
        ))

    channel = ctx.author.voice.channel
    vc = ctx.guild.voice_client

    if not vc:
        try:
            vc = await channel.connect(timeout=30)
        except asyncio.TimeoutError:
            return await ctx.send(embed=discord.Embed(
                description="❌ 음성 채널 연결 시간 초과! 다시 시도해주세요.", color=0xED4245
            ))
        except Exception as e:
            return await ctx.send(embed=discord.Embed(
                description=f"❌ 음성 채널 연결 실패: {e}", color=0xED4245
            ))
    elif vc.channel != channel:
        await vc.move_to(channel)

    state = get_state(ctx.guild.id)

    async with ctx.typing():
        try:
            msg = await ctx.send(embed=discord.Embed(
                description="🔍 검색 중...", color=0xFEE75C
            ))
            # URL인지 검색어인지 판별
            if not query.startswith("http"):
                query = f"ytsearch:{query}"

            data = await bot.loop.run_in_executor(
                None, lambda: ytdl.extract_info(query, download=False)
            )
            if data is None:
                return await msg.edit(embed=discord.Embed(
                    description="❌ 영상 정보를 불러올 수 없어요.", color=0xED4245
                ))
            if 'entries' in data:
                data = data['entries'][0]

            song_info = {
                "url": data.get('webpage_url') or data.get('url'),
                "title": data.get('title', '제목 없음'),
                "duration": YTDLSource._format_duration(data.get('duration', 0)),
                "thumbnail": data.get('thumbnail', ''),
                "requester": ctx.author.mention
            }

            await msg.delete()

        except Exception as e:
            return await ctx.send(embed=discord.Embed(
                description=f"❌ 오류 발생: {e}", color=0xED4245
            ))

    # 재생 중이면 대기열에 추가
    if vc.is_playing() or vc.is_paused():
        state["queue"].append(song_info)
        embed = discord.Embed(
            title="📥 대기열에 추가됨",
            description=f"**[{song_info['title']}]({song_info['url']})**",
            color=0xFEE75C
        )
        embed.add_field(name="⏱ 길이", value=song_info["duration"], inline=True)
        embed.add_field(name="📋 대기 순서", value=f"{len(state['queue'])}번째", inline=True)
        embed.add_field(name="👤 요청자", value=song_info["requester"], inline=True)
        if song_info["thumbnail"]:
            embed.set_thumbnail(url=song_info["thumbnail"])
        embed.set_footer(text="음악봇 🎧")
        await ctx.send(embed=embed)
    else:
        state["queue"].append(song_info)
        await play_next(ctx)


@bot.command(name="멈춰", aliases=["stop", "s"])
async def stop(ctx):
    """음악 종료 및 봇 퇴장"""
    state = get_state(ctx.guild.id)
    state["queue"].clear()
    state["now_playing"] = None
    vc = ctx.guild.voice_client

    if vc and vc.is_connected():
        vc.stop()
        await vc.disconnect()
        await ctx.send(embed=discord.Embed(
            title="⏹ 음악 종료",
            description="재생을 멈추고 봇이 퇴장했어요!",
            color=0xED4245
        ))
    else:
        await ctx.send(embed=discord.Embed(
            description="❌ 봇이 음성 채널에 없어요!", color=0xED4245
        ))


@bot.command(name="스킵", aliases=["skip", "sk"])
async def skip(ctx):
    """현재 곡 스킵"""
    vc = ctx.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
        await ctx.send(embed=discord.Embed(
            title="⏭ 스킵",
            description="다음 곡으로 넘어갔어요!",
            color=0xFEE75C
        ))
    else:
        await ctx.send(embed=discord.Embed(
            description="❌ 재생 중인 노래가 없어요!", color=0xED4245
        ))


@bot.command(name="일시정지", aliases=["pause"])
async def pause(ctx):
    """일시정지"""
    vc = ctx.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await ctx.send(embed=discord.Embed(
            description="⏸ 일시정지했어요!", color=0xFEE75C
        ))
    else:
        await ctx.send(embed=discord.Embed(
            description="❌ 재생 중인 노래가 없어요!", color=0xED4245
        ))


@bot.command(name="재개", aliases=["resume", "r"])
async def resume(ctx):
    """재개"""
    vc = ctx.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await ctx.send(embed=discord.Embed(
            description="▶️ 재개했어요!", color=0x57F287
        ))
    else:
        await ctx.send(embed=discord.Embed(
            description="❌ 일시정지된 노래가 없어요!", color=0xED4245
        ))


@bot.command(name="볼륨", aliases=["volume", "vol", "v"])
async def volume(ctx, vol: int):
    """볼륨 조절 (0~200)"""
    if not (0 <= vol <= 200):
        return await ctx.send(embed=discord.Embed(
            description="❌ 볼륨은 0~200 사이로 입력해주세요!", color=0xED4245
        ))

    state = get_state(ctx.guild.id)
    state["volume"] = vol / 100
    vc = ctx.guild.voice_client

    if vc and vc.source:
        vc.source.volume = state["volume"]

    await ctx.send(embed=discord.Embed(
        description=f"🔊 볼륨을 **{vol}%** 로 설정했어요!", color=0x57F287
    ))


@bot.command(name="반복", aliases=["loop", "l"])
async def loop(ctx):
    """현재 곡 반복 재생 토글"""
    state = get_state(ctx.guild.id)
    state["loop"] = not state["loop"]
    status = "켜짐" if state["loop"] else "꺼짐"
    await ctx.send(embed=discord.Embed(
        description=f"🔁 반복 재생: **{status}**", color=0x57F287
    ))


@bot.command(name="대기열", aliases=["queue", "q"])
async def queue_cmd(ctx):
    """현재 대기열 확인"""
    state = get_state(ctx.guild.id)
    await ctx.send(embed=make_queue_embed(state))


@bot.command(name="도움", aliases=["help", "h"])
async def help_cmd(ctx):
    """명령어 목록"""
    embed = discord.Embed(
        title="📖 음악봇 명령어 목록",
        description="접두사: `!` | 괄호 안은 단축 명령어",
        color=0x5865F2
    )
    embed.add_field(name="!실행 (p) [URL/검색어]", value="유튜브 음악 재생", inline=True)
    embed.add_field(name="!스킵 (sk)", value="다음 곡으로 스킵", inline=True)
    embed.add_field(name="!멈춰 (s)", value="재생 중단 및 퇴장", inline=True)
    embed.add_field(name="!일시정지 (pause)", value="일시정지", inline=True)
    embed.add_field(name="!재개 (r)", value="재개", inline=True)
    embed.add_field(name="!볼륨 (v) [0-200]", value="볼륨 조절", inline=True)
    embed.add_field(name="!반복 (l)", value="반복 재생 켜기/끄기", inline=True)
    embed.add_field(name="!대기열 (q)", value="대기열 확인", inline=True)
    embed.add_field(name="!재설정 (reset)", value="봇 재시작 (관리자 전용)", inline=True)
    embed.add_field(name="!종료 (quit)", value="봇 종료 (관리자 전용)", inline=True)
    embed.set_footer(text="음악봇 🎧")
    await ctx.send(embed=embed)


# ─────────────────────────────────────────
#  관리자 명령어
# ─────────────────────────────────────────
@bot.command(name="재설정", aliases=["reset", "restart"])
@commands.has_permissions(administrator=True)
async def reset_commands(ctx):
    """봇 명령어 재설정 및 재시작"""
    embed = discord.Embed(
        title="🔄 봇 재설정",
        description="봇을 재시작합니다...",
        color=0xFEE75C
    )
    await ctx.send(embed=embed)
    
    # 봇 재시작
    await bot.close()
    os.execv(sys.executable, ['python'] + sys.argv)


@bot.command(name="종료", aliases=["shutdown", "quit"])
@commands.has_permissions(administrator=True)
async def shutdown_bot(ctx):
    """봇 종료"""
    embed = discord.Embed(
        title="🛑 봇 종료",
        description="봇을 종료합니다...",
        color=0xED4245
    )
    await ctx.send(embed=embed)
    
    await bot.close()
    sys.exit(0)


# ─────────────────────────────────────────
#  봇 시작
# ─────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"✅ 로그인 완료: {bot.user}")
    
    # 슬래시 명령어 비활성화
    await bot.tree.sync()
    print("🔧 슬래시 명령어 초기화 완료")
    
    # 특정 채널에 도움말 자동 전송
    channel_id = 1490196027821789274
    channel = bot.get_channel(channel_id)
    
    if channel:
        embed = discord.Embed(
            title="📖 음악봇 명령어 목록",
            description="접두사: `!` | 괄호 안은 단축 명령어",
            color=0x5865F2
        )
        embed.add_field(name="!실행 (p) [URL/검색어]", value="유튜브 음악 재생", inline=True)
        embed.add_field(name="!스킵 (sk)", value="다음 곡으로 스킵", inline=True)
        embed.add_field(name="!멈춰 (s)", value="재생 중단 및 퇴장", inline=True)
        embed.add_field(name="!일시정지 (pause)", value="일시정지", inline=True)
        embed.add_field(name="!재개 (r)", value="재개", inline=True)
        embed.add_field(name="!볼륨 (v) [0-200]", value="볼륨 조절", inline=True)
        embed.add_field(name="!반복 (l)", value="반복 재생 켜기/끄기", inline=True)
        embed.add_field(name="!대기열 (q)", value="대기열 확인", inline=True)
        embed.add_field(name="!재설정 (reset)", value="봇 재시작 (관리자 전용)", inline=True)
        embed.add_field(name="!종료 (quit)", value="봇 종료 (관리자 전용)", inline=True)
        embed.set_footer(text="음악봇 🎧")
        
        await channel.send(embed=embed)
        print(f"📨 도움말을 채널 {channel_id}에 전송 완료")
    else:
        print(f"❌ 채널 {channel_id}를 찾을 수 없습니다")
    
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="!도움"
    ))

bot.run(TOKEN)