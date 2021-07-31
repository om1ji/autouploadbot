import re
import os
from subprocess import check_output
from telebot import TeleBot
from config import API_TOKEN, CHANNEL

YOUTUBE_LINK_REGEX = r"http(?:s?):\/\/(?:www\.)?youtu(?:be\.com\/watch\?v=|\.be\/)([\w\-\_]*)(&(amp;)?‌​[\w\?‌​=]*)?"

bot = TeleBot(API_TOKEN, parse_mode='MarkdownV2')

def extract_link(raw):
    raw = raw.decode('utf-8')
    matches = re.search(r'(?<=href\=")(.+?)(?=")', raw)
    link = matches.group(0).strip()
    return link

def extract_channel_name(raw):
    raw = raw.decode('utf-8')
    matches = re.search(r"(?<=\<\name\>).+(?=\</\name\>)", raw)
    return matches.group(0).strip()

def send_release(message=None, rawdata=None):
    if rawdata is not None:
        link = extract_link(rawdata.data)
    elif message is not None:
        link = message

    os.system(fr"""youtube-dl -x {link} --audio-format mp3 --audio-quality 0 -o "%(title)s.%(ext)s" --encoding 'utf-8' -w --embed-thumbnail --metadata-from-title "%(artist)s - %(title)s" """)

    basename = str(check_output(f'youtube-dl {link} -e', shell=True))[2:-3]

    audio_file_path = basename + '.mp3'
    thumb = str(check_output(f'youtube-dl {link} --get-thumbnail', shell=True))[2:-3]
    
    if rawdata is not None:
        bot.send_audio(str(CHANNEL), 
                    audio=open(audio_file_path, 'rb'), 
                    thumb=thumb, 
                    timeout=60)
    else:
        return open(audio_file_path, 'rb')

@bot.message_handler(regexp=YOUTUBE_LINK_REGEX)
def send(message):
    matches = re.search(YOUTUBE_LINK_REGEX, message.text)
    text = matches.group(0).strip()
    bot.send_audio(message.chat.id, send_release(message=text))

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.reply_to(message, message.text)

if __name__=='__main__':
    bot.polling(none_stop=True)