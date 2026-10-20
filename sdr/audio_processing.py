"""Local bounded decoding and contiguous, silence-aware ASR segments."""
from array import array
from contextlib import contextmanager
from pathlib import Path
import io
import subprocess
import sys
import tempfile
import wave

RATE = 16000
PIPELINE_VERSION = 'pcm16-16k-mono-silence-v1'


class AudioError(Exception):
    pass


@contextmanager
def normalized_audio(source, directory, settings):
    try:
        from imageio_ffmpeg import get_ffmpeg_exe
        executable = get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        raise AudioError('长录音处理组件未安装，请执行 pip install -r requirements.txt 后重试') from None
    with tempfile.TemporaryDirectory(prefix='.processing-', dir=directory) as work:
        output = Path(work) / 'normalized.wav'
        command = [executable, '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror',
                   '-protocol_whitelist', 'file,pipe', '-format_whitelist', 'wav,mp3,mov,flac,ogg',
                   '-i', str(Path(source).resolve()), '-map', '0:a:0', '-vn', '-sn', '-dn',
                   '-ac', '1', '-ar', str(RATE), '-c:a', 'pcm_s16le',
                   '-t', str(settings.audio_max_seconds+1), '-threads', '1', '-f', 'wav', str(output)]
        try:
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=settings.audio_decode_timeout,
                                    check=False)
        except subprocess.TimeoutExpired:
            raise AudioError('录音解码超时，原文件已保存，可重新导出为标准WAV或MP3后上传') from None
        except OSError:
            raise AudioError('无法启动本机音频处理组件，请检查FFmpeg安装') from None
        if result.returncode or not output.exists():
            raise AudioError('录音解码失败，可能是文件不完整或编码不兼容；请重新导出为标准WAV或MP3')
        output.chmod(0o600)
        try:
            with wave.open(str(output), 'rb') as audio:
                duration = audio.getnframes()/RATE
                if audio.getnchannels()!=1 or audio.getsampwidth()!=2 or audio.getframerate()!=RATE or not audio.getnframes():
                    raise AudioError('录音解码后没有有效音频数据')
                if duration > settings.audio_max_seconds:
                    raise AudioError(f'实际解码时长超过{settings.audio_max_seconds/60:g}分钟，请拆分录音后上传')
        except (wave.Error, EOFError):
            raise AudioError('录音转换结果不完整，请重新导出音频') from None
        yield output


def segment_ranges(audio, seconds):
    """Each input frame belongs to exactly one segment; never remove repeated speech."""
    if not 1 <= seconds <= 180:
        raise AudioError('分段配置无效，每段须在1到180秒之间')
    total, start, window = audio.getnframes(), 0, RATE//5
    result = []
    while start < total:
        end = min(start+seconds*RATE, total)
        if end < total:
            lower = max(start+int(seconds*.75)*RATE, end-8*RATE)
            following_quiet = False
            for offset in range(end-window, lower-1, -window):
                audio.setpos(offset)
                values = array('h', audio.readframes(window))
                if sys.byteorder != 'little':
                    values.byteswap()
                quiet = bool(values) and max(values)<300 and min(values)>-300
                if quiet and following_quiet:
                    end = offset+window
                    break
                following_quiet = quiet
        result.append((start,end))
        start = end
    return result


def wav_bytes(frames):
    output = io.BytesIO()
    with wave.open(output,'wb') as writer:
        writer.setnchannels(1);writer.setsampwidth(2);writer.setframerate(RATE)
        writer.writeframes(frames)
    return output.getvalue()
