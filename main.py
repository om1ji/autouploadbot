# -*- coding: utf-8 -*-

import telebot
from flask import Flask, request
import re
import os
from subprocess import check_output

API_TOKEN = input("Token: ")
CHANNEL = input("Channel ID or @username: ")
APP = Flask(__name__)

bot = telebot.TeleBot(API_TOKEN, parse_mode='MarkdownV2')

def extract_link(raw):
    raw = raw.decode('utf-8')
    matches = re.search(r'(?<=href\=")(.+?)(?=")', raw)
    link = matches.group(0).strip()
    return link

@APP.route('/', methods=['GET','POST'])
def receive_webhook():
    if request.method == 'GET':
        print(request)
        if request.args.get('hub.challenge', ''):
            return request.args.get('hub.challenge', '')

    elif request.method == 'POST':
        link = extract_link(request.data)
        os.system(fr"""youtube-dl -x {link} --audio-format mp3 --audio-quality 0 -o "%(title)s.%(ext)s" --embed-thumbnail --metadata-from-title "%(artist)s - %(title)s" """)

        basename = str(check_output(f'youtube-dl {link} -e'))[2:-3]

        audio_file_path = basename + '.mp3'
        thumb = str(check_output(f'youtube-dl {link} --get-thumbnail'))[2:-3]
        bot.send_audio(str(CHANNEL), audio=open(audio_file_path, 'rb'), thumb=thumb, timeout=60)
    return '200'

if __name__ == '__main__':
    bot.polling()
