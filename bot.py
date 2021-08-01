import re
import os
from subprocess import check_output
from telebot import TeleBot
from config import API_TOKEN, CHANNEL
from json import loads
import sqlite3

YOUTUBE_LINK_REGEX = r"http(?:s?):\/\/(?:www\.)?youtu(?:be\.com\/watch\?v=|\.be\/)([\w\-\_]*)(&(amp;)?‌​[\w\?‌​=]*)?"

bot = TeleBot(API_TOKEN)

def extract_link(raw):
    raw = raw.decode('utf-8')
    matches = re.search(r'(?<=href\=")(.+?)(?=")', raw)
    link = matches.group(0).strip()
    return link

def str2sec(x):
    m, s = x.strip().split(':')
    return int(m)*60 + int(s)

def send_release(message=None, rawdata=None):

    con = sqlite3.connect('files_ids.db')
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS file_ids
                    (video_id, file_id)""")
    con.commit()

    if rawdata is not None:
        link = extract_link(rawdata.data)
    elif message is not None:
        link = message

    os.system(fr"""youtube-dl -x {link} --audio-format mp3 --audio-quality 0 -o "%(title)s.%(ext)s" --encoding 'utf-8' -w --embed-thumbnail --metadata-from-title "%(artist)s - %(title)s" """)

    info = loads(check_output(f'youtube-dl -s -j {link}', shell=True))

    duration = info['duration']
    channel_name = info['uploader']
    video_id = info['display_id']
    basename = info['title']

    audio_file_path = basename + '.mp3'

    if rawdata is not None:
        caption = "#"+channel_name

        if cur.execute("SELECT EXISTS(SELECT 1 FROM file_ids WHERE video_id='{video_id}'", )

        bot.send_audio(CHANNEL, 
                    audio=open(audio_file_path, 'rb'), 
                    caption=caption,
                    duration=duration,
                    timeout=60)
    else:
        return open(audio_file_path, 'rb')

@bot.message_handler(regexp=YOUTUBE_LINK_REGEX)
def send(message):
    text = re.search(YOUTUBE_LINK_REGEX, message.text).group(0).strip()
    bot.send_audio(message.chat.id, send_release(message=text))

@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    bot.send_message(message.chat.id, """How to use bot:

Send me a YouTube link. I will response you with an audio file. 

That is the only thing I can do. All the messages will be echoed since I am not well developed yet. Autoposting to channels feature will be introduced soon.""")

@bot.message_handler(func=lambda message: True)
def echo(message):
    bot.reply_to(message, message.text)

if __name__=='__main__':
    bot.polling(none_stop=True)

# TODO Поддержка UTF8 в :47
# TODO Логи
# TODO Очередь в базе данных